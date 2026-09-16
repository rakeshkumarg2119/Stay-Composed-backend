import asyncio
import sys
from app.database import (
    items_collection,
    claims_collection,
    staff_collection,
    blood_alerts_collection,
    chat_threads_collection,
    chat_messages_collection,
    device_tokens_collection,
    ensure_indexes,
)

async def clear_database(keep_staff: bool = False):
    print("=" * 60)
    print("  Stay Composed — MongoDB Cleanup Utility")
    print("=" * 60)
    
    # 1. Clear items (lost complaints and found reports)
    res_items = await items_collection().delete_many({})
    print(f"[-] Deleted Items (Lost & Found):       {res_items.deleted_count}")

    # 2. Clear claims & verification attempts
    res_claims = await claims_collection().delete_many({})
    print(f"[-] Deleted Claim Attempts:              {res_claims.deleted_count}")

    # 3. Clear chat threads & messages
    res_threads = await chat_threads_collection().delete_many({})
    res_msgs = await chat_messages_collection().delete_many({})
    print(f"[-] Deleted Chat Threads:                {res_threads.deleted_count}")
    print(f"[-] Deleted Chat Messages:               {res_msgs.deleted_count}")

    # 4. Clear blood alert history
    res_blood = await blood_alerts_collection().delete_many({})
    print(f"[-] Deleted Blood Alerts:                {res_blood.deleted_count}")

    # 5. Clear device push notification tokens
    res_tokens = await device_tokens_collection().delete_many({})
    print(f"[-] Deleted Device Push Tokens:          {res_tokens.deleted_count}")

    # 6. Staff directory
    if not keep_staff:
        res_staff = await staff_collection().delete_many({})
        print(f"[-] Deleted Staff Directory Entries:     {res_staff.deleted_count}")
    else:
        print("[*] Kept Staff Directory Entries intact.")

    # Re-apply indexes cleanly
