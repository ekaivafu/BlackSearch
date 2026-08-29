from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder

def get_main_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    
    # User buttons
    builder.button(text="📱 Number Info")
    builder.button(text="🪪 Aadhar Info")
    builder.button(text="📧 Email Info")
    builder.button(text="📊 My Status")
    
    if not is_admin:
        builder.button(text="💳 Request Recharge")
        
    # Admin buttons
    if is_admin:
        builder.button(text="⚙️ Manage Users")
        builder.button(text="💰 Manage Points")
    
    # Adjust layout
    if is_admin:
        builder.adjust(2, 2, 2)
    else:
        builder.adjust(2, 2, 1)
        
    return builder.as_markup(resize_keyboard=True)
