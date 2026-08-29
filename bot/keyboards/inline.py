from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

def get_approval_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Approve", callback_data=f"approve_{telegram_id}")
    builder.button(text="❌ Reject", callback_data=f"reject_{telegram_id}")
    return builder.as_markup()

def get_recharge_approval_keyboard(request_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Approve", callback_data=f"recharge_approve_{request_id}")
    builder.button(text="❌ Reject", callback_data=f"recharge_reject_{request_id}")
    return builder.as_markup()

def get_recharge_request_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Request Recharge", callback_data="request_recharge")
    return builder.as_markup()

def get_recharge_amounts_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="10 Credits", callback_data="recharge_amount_10")
    builder.button(text="50 Credits", callback_data="recharge_amount_50")
    builder.button(text="100 Credits", callback_data="recharge_amount_100")
    builder.button(text="Cancel", callback_data="cancel_recharge")
    builder.adjust(3, 1)
    return builder.as_markup()

def get_payment_packages_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="₹50 for 15 searches", callback_data="buy_package_15")
    builder.button(text="₹100 for 40 searches", callback_data="buy_package_40")
    builder.button(text="₹200 for 1 day unlimited", callback_data="buy_package_-1")
    builder.button(text="₹700 for 7 days unlimited", callback_data="buy_package_-7")
    builder.adjust(1)
    return builder.as_markup()

def get_search_type_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📱 Phone Number", callback_data="search_type_phone")
    builder.button(text="🪪 Aadhaar", callback_data="search_type_aadhar")
    builder.adjust(2)
    return builder.as_markup()
