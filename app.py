import streamlit as st
import datetime
from typing import List, Dict
import json
import time
import requests
import pandas as pd
import uuid
from urllib.parse import urlparse

from spliit.client import Spliit, SplitMode
import monobank

# -------------------------------------------------------------------
# MCC Code handling
# -------------------------------------------------------------------
@st.cache_data(ttl=3600)  # Cache for 1 hour
def get_mcc_codes() -> Dict[str, str]:
    """Fetch MCC codes from a public source and cache them."""
    try:
        # Using a public MCC codes source with proper format
        response = requests.get('https://raw.githubusercontent.com/greggles/mcc-codes/main/mcc_codes.json')
        response.raise_for_status()
        mcc_data = response.json()
        return {str(item["mcc"]): item["edited_description"] 
                for item in mcc_data if "mcc" in item and "edited_description" in item}
    except Exception as e:
        st.warning(f"Failed to fetch MCC codes: {str(e)}")
        return {}

def get_mcc_description(mcc: str) -> str:
    """Get description for an MCC code."""
    mcc_codes = get_mcc_codes()
    return mcc_codes.get(str(mcc), f"Unknown ({mcc})")

# -------------------------------------------------------------------
# Helper Functions
# -------------------------------------------------------------------
def get_participant_info(spliit_client, selected_participant):
    """Get participant info and paid_for shares."""
    participants = spliit_client.get_participants()
    payer_id = participants[selected_participant]
    # Convert percentage to basis points (multiply by 100)
    # e.g., 25% becomes 2500, 33.33% becomes 3333
    paid_for = [(pid, int(st.session_state["participant_shares"][name] * 100)) 
                for name, pid in participants.items()]
    return payer_id, paid_for

def upload_transaction_to_spliit(spliit_client, payer_id, paid_for, title, amount, category=None, transaction_id=None, date=None):
    """Upload a single transaction to Spliit."""
    # Convert amount to cents (Spliit uses integer amounts)
    amount_cents = int(amount * 100)
    
    # Add the expense
    response = spliit_client.add_expense(
        title=f"{title} mono-{transaction_id}" if transaction_id else title,
        amount=amount_cents,
        paid_by=payer_id,
        paid_for=paid_for,  # [(participant_id, shares), ...] where shares are in basis points
        notes=f"Category: {category}" if category else "",
        split_mode=SplitMode.BY_PERCENTAGE,
        expense_date=date if date else datetime.date.today()
    )
    
    # Parse response to verify success
    try:
        response_data = json.loads(response)
        if response_data and len(response_data) > 0:
            result = response_data[0].get("result", {}).get("data", {}).get("json", {})
            if result.get("expenseId"):
                return True
    except Exception as e:
        st.error(f"Failed to parse Spliit response: {str(e)}")
    return False

def upload_transactions_batch(spliit_client, payer_id, paid_for, transactions):
    """Upload multiple transactions to Spliit."""
    success_count = 0
    for transaction in transactions:
        try:
            success = upload_transaction_to_spliit(
                spliit_client,
                payer_id,
                paid_for,
                title=transaction["name"],
                amount=transaction["amount"],
                category=transaction.get("category", "N/A"),
                transaction_id=transaction.get("id"),
                date=transaction.get("date", datetime.date.today())
            )
            if success:
                success_count += 1
            else:
                st.error(f"Failed to upload transaction '{transaction['name']}': Invalid response from Spliit")
        except Exception as e:
            st.error(f"Failed to upload transaction '{transaction['name']}': {str(e)}")
    return success_count

def upload_to_spliit(transactions_to_upload):
    """Common function to handle uploading transactions to Spliit"""
    if not st.session_state["spliit_client"]:
        st.error("No group information. Please fetch the group first.")
        return False
    elif not st.session_state["selected_participant"]:
        st.error("Please select a participant first.")
        return False
    
    try:
        # Get participant info
        payer_id, paid_for = get_participant_info(
            st.session_state["spliit_client"],
            st.session_state["selected_participant"]
        )
        
        # Filter out empty rows for manual/CSV entries
        valid_transactions = [
            t for t in transactions_to_upload 
            if t.get("name", "").strip() and t.get("amount", 0) > 0
        ]
        
        if not valid_transactions:
            st.warning("No valid transactions to upload. Make sure transactions have at least a name and amount.")
            return False
        
        success_count = upload_transactions_batch(
            st.session_state["spliit_client"],
            payer_id,
            paid_for,
            valid_transactions
        )
        
        if success_count > 0:
            st.success(f"Successfully uploaded {success_count} transaction(s) to Spliit.")
            return True
    
    except Exception as e:
        st.error(f"Failed to upload transactions: {str(e)}")
        return False
    
    return False

def render_upload_section(transactions_to_upload, on_success=None):
    """Common function to render upload button and group link"""
    col1, col2 = st.columns([3, 1])
    with col1:
        if st.button("Upload to Spliit"):
            if upload_to_spliit(transactions_to_upload) and on_success:
                on_success()
    with col2:
        if st.session_state.get("group_url"):
            st.link_button("Open Group in Browser", st.session_state["group_url"])

def reset_manual_entry():
    """Reset the manual entry data editor to a single empty row"""
    st.session_state["manual_transactions"] = [{
        "amount": 0.0,
        "name": "",
        "category": "",
        "date": datetime.date.today()
    }]

# -------------------------------------------------------------------
# Streamlit App
# -------------------------------------------------------------------

st.set_page_config(page_title="Spliit Importer", layout="wide")

st.title("Spliit Importer")

# 1. Ask for Spliit Group
st.header("Step 1: Spliit Group")
default_group = st.secrets.get("spliit_group", "")
group_url = st.text_input("Enter Spliit Group URL", value=default_group, help="Example: https://spliit.app/groups")
fetch_group_button = st.button("Fetch Group")

if "group_data" not in st.session_state:
    st.session_state["group_data"] = None
if "selected_participant" not in st.session_state:
    st.session_state["selected_participant"] = None
if "spliit_client" not in st.session_state:
    st.session_state["spliit_client"] = None
if "mono_client" not in st.session_state:
    st.session_state["mono_client"] = None
if "mono_accounts" not in st.session_state:
    st.session_state["mono_accounts"] = None
if "participant_shares" not in st.session_state:
    st.session_state["participant_shares"] = {}
if "group_url" not in st.session_state:
    st.session_state["group_url"] = None

if fetch_group_button:
    if not group_url.strip():
        st.warning("Please enter a valid Spliit group URL.")
    else:
        try:
            # Parse the URL to get server_url and group_id
            parsed_url = urlparse(group_url)
            server_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
            group_id = parsed_url.path.split("/")[-1]
            
            # Store the group URL for later use
            st.session_state["group_url"] = group_url
            
            # Initialize Spliit client with server_url
            st.session_state["spliit_client"] = Spliit(group_id=group_id, server_url=server_url)
            # Get group data
            st.session_state["group_data"] = st.session_state["spliit_client"].get_group()
            # Initialize equal shares for all participants
            participants = st.session_state["spliit_client"].get_participants()
            equal_share = int(100 / len(participants))  # Integer division for base share
            remaining_participants = len(participants) - 1
            last_participant_share = 100 - (equal_share * remaining_participants)  # Calculate exact remainder
            
            # Initialize all participants with equal share except the last one
            st.session_state["participant_shares"] = {name: equal_share for name in list(participants.keys())[:-1]}
            # Set the last participant's share to make total exactly 100
            st.session_state["participant_shares"][list(participants.keys())[-1]] = last_participant_share
            
            st.success(f"Group fetched: {st.session_state['group_data']['name']}")
        except Exception as e:
            st.error(f"Failed to fetch group: {str(e)}")

# If we have group data, let user select themselves from the participants
if st.session_state["group_data"] is not None:
    group = st.session_state["group_data"]
    participants = st.session_state["spliit_client"].get_participants()
    
    st.subheader(f"Participants in {group.get('name')}")
    
    if participants:
        col1, col2 = st.columns([1, 2])
        
        with col1:
            selected_participant = st.selectbox(
                "Select yourself from the participants",
                ["-- Select yourself --"] + list(participants.keys()),
                index=0
            )
            if selected_participant != "-- Select yourself --":
                st.session_state["selected_participant"] = selected_participant
            else:
                st.session_state["selected_participant"] = None
                st.info("Please select yourself to continue")
        
        # Only show shares and further options if participant is selected
        if st.session_state["selected_participant"]:
            with col2:
                st.write("Set share percentages for each participant:")
                total_share = 0
                cols = st.columns(len(participants))
                
                # Reorder participants to put selected user first
                participant_names = list(participants.keys())
                participant_names.remove(st.session_state["selected_participant"])
                participant_names.insert(0, st.session_state["selected_participant"])
                
                for i, name in enumerate(participant_names):
                    with cols[i]:
                        share_label = "Your share" if name == st.session_state["selected_participant"] else f"{name}'s share"
                        st.session_state["participant_shares"][name] = st.number_input(
                            share_label,
                            min_value=0,
                            max_value=100,
                            value=st.session_state["participant_shares"].get(name, 0),
                            step=1,
                            key=f"share_{name}"
                        )
                        total_share += st.session_state["participant_shares"][name]
                
                if total_share != 100:
                    st.warning(f"Total share must be 100% (current: {total_share}%)")
    else:
        st.warning("No participants found in this group.")

    # Only show next steps if participant is selected
    if st.session_state["selected_participant"]:
        # 2. Let user select import method: Monobank or Manual
        st.header("Step 2: Choose Import Method")
        import_method = st.radio(
            "How do you want to add transactions?",
            ("Monobank", "Manual")
        )

        if "transactions" not in st.session_state:
            st.session_state["transactions"] = []  # store all transactions (amount, name, category, etc.)

        # 2A. If Monobank is chosen
        if import_method == "Monobank":
            st.subheader("Monobank Import")
            
            # Token input and account fetching
            default_token = st.secrets.get("monobank_token", "")
            monobank_token = st.text_input("Monobank Token", 
                                          value=default_token,
                                          type="password", 
                                          help="Get your token at https://api.monobank.ua/")
            fetch_accounts_button = st.button("Fetch Accounts")
            
            if fetch_accounts_button and monobank_token:
                try:
                    # Initialize Monobank client
                    mono = monobank.Client(monobank_token)
                    # Get client info with accounts
                    client_info = mono.get_client_info()
                    
                    # Store client and accounts in session state
                    st.session_state["mono_client"] = mono
                    st.session_state["mono_accounts"] = client_info.get("accounts", [])
                    
                    st.success(f"Successfully connected to Monobank as {client_info.get('name', 'Unknown')}")
                except monobank.TooManyRequests:
                    st.error("Too many requests to Monobank API. Please wait a moment and try again.")
                except Exception as e:
                    st.error(f"Failed to connect to Monobank: {str(e)}")
            
            # Account selection
            if st.session_state["mono_accounts"]:
                # Format account info for display
                account_options = {}
                for acc in st.session_state["mono_accounts"]:
                    currency_code = acc.get("currencyCode", 980)  # 980 is UAH
                    acc_type = acc.get("type")
                    masked_id = acc.get("id", "")[-4:]
                    balance = acc.get("balance", 0)
                    label = f"{acc_type} {balance} UAH (ID: ...{masked_id})"
                    account_options[acc["id"]] = label
                
                selected_account = st.selectbox(
                    "Select Account",
                    options=list(account_options.keys()),
                    format_func=lambda x: account_options[x]
                )
                
                # Date range selection
                col1, col2 = st.columns(2)
                with col1:
                    default_start = datetime.date.today() - datetime.timedelta(days=30)  # Default to 30 days
                    start_date = st.date_input("Start Date", default_start)
                with col2:
                    end_date = st.date_input("End Date", datetime.date.today())

                fetch_statements_button = st.button("Fetch Statements")

                if fetch_statements_button:
                    if start_date > end_date:
                        st.error("Start date must be on or before end date.")
                    else:
                        try:
                            all_statements = []
                            current_to_date = end_date
                            progress_bar = st.progress(0)
                            status_text = st.empty()
                            
                            while True:
                                # Calculate the from date for this chunk (31 days or less)
                                current_from_date = max(
                                    start_date,
                                    current_to_date - datetime.timedelta(days=30)  # Use 30 to ensure we don't exceed 31 days
                                )
                                
                                status_text.text(f"Fetching transactions from {current_from_date} to {current_to_date}...")
                                
                                try:
                                    # Get statements for current period - using date objects directly
                                    statements = st.session_state["mono_client"].get_statements(
                                        selected_account,
                                        current_from_date,
                                        current_to_date
                                    )
                                    
                                    if statements:
                                        all_statements.extend(statements)
                                        status_text.text(f"Found {len(all_statements)} transactions so far...")
                                    
                                    # Update progress
                                    total_days = (end_date - start_date).days
                                    days_processed = (end_date - current_from_date).days
                                    progress = min(1.0, days_processed / total_days)
                                    progress_bar.progress(progress)
                                    
                                    # If we've reached or passed the start date, we're done
                                    if current_from_date <= start_date:
                                        break
                                    
                                    # Set the next end date to one day before the current from date
                                    current_to_date = current_from_date - datetime.timedelta(days=1)
                                    
                                    # Wait before next request to respect rate limits
                                    for i in range(5, 0, -1):
                                        status_text.text(f"Rate limit cooldown: {i} seconds...")
                                        time.sleep(1)
                                        
                                except monobank.TooManyRequests:
                                    status_text.text("Rate limit reached. Waiting 5 seconds...")
                                    time.sleep(5)
                                    continue
                                    
                                except Exception as e:
                                    st.error(f"Error fetching chunk: {str(e)}")
                                    break

                            progress_bar.progress(1.0)
                            status_text.text("Processing transactions...")

                            # Convert them into our transaction format
                            new_transactions = []
                            for s in all_statements:
                                # Convert amount from cents to currency
                                amount = abs(s.get("amount", 0)) / 100.0
                                # Convert Unix timestamp to datetime
                                transaction_date = datetime.datetime.fromtimestamp(s.get("time", 0))
                                
                                new_transactions.append({
                                    "id": s.get("id"),
                                    "amount": amount,
                                    "name": s.get("description", "N/A"),
                                    "mcc": str(s.get("mcc", "")),
                                    "category": get_mcc_description(str(s.get("mcc", ""))),
                                    "selected": False,  # Default to unselected
                                    "date": transaction_date
                                })

                            # st.write(new_transactions)
                            # Replace the transactions in session_state
                            st.session_state["transactions"] = new_transactions
                            status_text.empty()
                            st.success(f"Fetched {len(new_transactions)} transactions from Monobank.")
                        except monobank.TooManyRequests:
                            st.error("Too many requests to Monobank API. Please wait a moment and try again.")
                        except Exception as e:
                            st.error(f"Failed to fetch statements: {str(e)}")

            # Only show transaction review and upload steps for Monobank import
            if import_method == "Monobank" and len(st.session_state["transactions"]) > 0:
                # 3. Display the transaction table
                st.header("Step 3: Review Transactions")

                if len(st.session_state["transactions"]) > 0:
                    st.write("Below are the transactions you have fetched/entered:")
                    
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        # Add filtering options
                        all_categories = list(set(t["category"] for t in st.session_state["transactions"] if t["category"]))
                        selected_categories = st.multiselect(
                            "Filter by categories",
                            options=all_categories,
                            default=[],
                            help="Select one or more categories to filter the transactions"
                        )
                    
                    with col2:
                        # Add Select All button
                        if st.button("Select All"):
                            for t in st.session_state["transactions"]:
                                t["selected"] = True
                        if st.button("Unselect All"):
                            for t in st.session_state["transactions"]:
                                t["selected"] = False
                    
                    # Show all transactions without filtering
                    edited_transactions = st.data_editor(
                        st.session_state["transactions"],
                        column_config={
                            "id": None,  # Hide the ID column
                            "date": st.column_config.DateColumn(
                                "Date",
                                help="Transaction date",
                                format="YYYY-MM-DD HH:MM:SS",
                                required=True
                            ),
                            "amount": st.column_config.NumberColumn(
                                "Amount",
                                help="Transaction amount",
                                min_value=0,
                                format="%.2f UAH",
                            ),
                            "name": st.column_config.TextColumn(
                                "Description",
                                help="Transaction description",
                            ),
                            "mcc": st.column_config.TextColumn(
                                "MCC Code",
                                help="Merchant Category Code",
                            ),
                            "category": st.column_config.TextColumn(
                                "Category",
                                help="Transaction category based on MCC code",
                            ),
                            "selected": st.column_config.CheckboxColumn(
                                "Select",
                                help="Select transactions to upload",
                                default=True,
                            ),
                        },
                        hide_index=True,
                        num_rows="dynamic",
                        use_container_width=True,
                    )
                    
                    # 4. Upload to Spliit
                    st.header("Step 4: Upload to Spliit")
                    selected_transactions = [t for t in edited_transactions if t.get("selected", False)]
                    render_upload_section(selected_transactions, 
                                       on_success=lambda: setattr(st.session_state, "transactions", 
                                                                [t for t in edited_transactions if not t.get("selected", False)]))
                else:
                    st.info("No transactions found yet. Use Monobank or Manual entry above.")

        # 2B. If Manual Entry is chosen
        else:
            st.subheader("Manual Import")
            
            # Add tabs for manual entry and CSV upload
            tab1, tab2 = st.tabs(["Manual Entry", "CSV Import"])
            
            with tab1:
                # Initialize manual transactions in session state if not exists
                if "manual_transactions" not in st.session_state:
                    st.session_state["manual_transactions"] = [{
                        "amount": 0.0,
                        "name": "",
                        "category": "",
                        "date": datetime.date.today()
                    }]
                
                edited_manual_transactions = st.data_editor(
                    st.session_state["manual_transactions"],
                    column_config={
                        "amount": st.column_config.NumberColumn(
                            "Amount",
                            help="Transaction amount",
                            min_value=0,
                            format="%.2f UAH",
                            required=True
                        ),
                        "name": st.column_config.TextColumn(
                            "Description",
                            help="Transaction description",
                            required=True
                        ),
                        "category": st.column_config.TextColumn(
                            "Category",
                            help="Transaction category (optional)"
                        ),
                        "date": st.column_config.DateColumn(
                            "Date",
                            help="Transaction date",
                            required=True,
                            default=datetime.date.today()
                        ),
                    },
                    num_rows="dynamic",
                    use_container_width=True,
                    hide_index=True
                )
                
                # Upload section
                render_upload_section(edited_manual_transactions, on_success=reset_manual_entry)
            
            with tab2:
                st.write("Upload a CSV file with the following columns:")
                example_df = pd.DataFrame([
                    [100.50, "Groceries", "Food"],
                    [25.00, "Coffee", "Drinks"]
                ], columns=["amount", "description", "category"])
                
                col1, col2 = st.columns([2, 1])
                with col1:
                    st.write("Example format:")
                    st.dataframe(example_df, hide_index=True)
                with col2:
                    st.download_button(
                        "Download Example CSV",
                        example_df.to_csv(index=False),
                        "example.csv",
                        "text/csv",
                        help="Download an example CSV file with the correct format"
                    )
                
                uploaded_file = st.file_uploader("Choose a CSV file", type="csv")
                if uploaded_file is not None:
                    try:
                        # Read CSV content
                        import csv
                        import io
                        
                        if not st.session_state["spliit_client"]:
                            st.error("No group information. Please fetch the group first.")
                        elif not st.session_state["selected_participant"]:
                            st.error("Please select a participant first.")
                        else:
                            # Get participant info once for all transactions
                            payer_id, paid_for = get_participant_info(
                                st.session_state["spliit_client"],
                                st.session_state["selected_participant"]
                            )
                            
                            success_count = 0
                            error_count = 0
                            
                            # Create a text area to show progress
                            progress_area = st.empty()
                            
                            # Read and process the CSV file
                            stringio = io.StringIO(uploaded_file.getvalue().decode("utf-8-sig"))  # utf-8-sig handles BOM
                            reader = csv.reader(stringio)
                            next(reader)  # Skip header row
                            
                            transactions = []
                            row_number = 1  # Start counting after header
                            for row in reader:
                                try:
                                    if len(row) < 2:
                                        continue  # Skip invalid rows
                                        
                                    # Parse amount and description (required)
                                    amount = float(row[0].strip())
                                    description = row[1].strip()
                                    
                                    # Parse category (optional)
                                    category = row[2].strip() if len(row) > 2 else ""
                                    
                                    transactions.append({
                                        "amount": amount,
                                        "name": description,
                                        "category": category,
                                        "date": datetime.date.today()  # Use today's date for CSV imports
                                    })
                                    
                                except Exception as e:
                                    error_count += 1
                                    st.error(f"Error in row {row_number}: {str(e)}")
                                row_number += 1
                            
                            if transactions:
                                render_upload_section(transactions)
                                
                                if success_count > 0:
                                    st.success(f"Successfully uploaded {success_count} transactions to Spliit.")
                                    if error_count > 0:
                                        st.warning(f"Failed to upload {error_count} transactions. Check the errors above.")
                            
                    except Exception as e:
                        st.error(f"Error processing CSV: {str(e)}")
