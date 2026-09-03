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

def get_payment_packages_keyboard(plans: list = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if plans:
        for p in plans:
            icon = "🪙" if getattr(p, "plan_type", None) and p.plan_type.value == "credits" else "👑"
            btn_text = f"{icon} {p.name}"
            if f"₹{p.price}" not in p.name:
                btn_text += f" — ₹{p.price}"
            builder.button(text=btn_text, callback_data=f"buy_plan_{p.id}")
    else:
        builder.button(text="₹50 for 15 searches", callback_data="buy_package_15")
        builder.button(text="₹100 for 40 searches", callback_data="buy_package_40")
        builder.button(text="₹200 for 1 day unlimited", callback_data="buy_package_-1")
        builder.button(text="₹700 for 7 days unlimited", callback_data="buy_package_-7")
    builder.adjust(1)
    return builder.as_markup()

def get_admin_plans_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Create Plan", callback_data="admin_plan_create")
    builder.button(text="✏️ Edit Plan", callback_data="admin_plan_list_edit")
    builder.button(text="🗑️ Delete Plan", callback_data="admin_plan_list_delete")
    builder.button(text="🎁 Free Credits", callback_data="admin_edit_free_credits")
    builder.button(text="🔄 Refresh", callback_data="admin_plans_refresh")
    builder.button(text="❌ Close", callback_data="admin_plan_close")
    builder.adjust(2, 2, 2)
    return builder.as_markup()

def get_plan_type_selection_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🪙 Credit Based (fixed searches)", callback_data="create_type_credits")
    builder.button(text="👑 Unlimited Day Based", callback_data="create_type_days")
    builder.button(text="❌ Cancel", callback_data="plan_cancel")
    builder.adjust(1)
    return builder.as_markup()

def get_plans_selection_keyboard(plans: list, action_prefix: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for p in plans:
        icon = "🪙" if getattr(p, "plan_type", None) and p.plan_type.value == "credits" else "👑"
        builder.button(text=f"{icon} {p.name} (₹{p.price})", callback_data=f"{action_prefix}_{p.id}")
    builder.button(text="🔙 Back to Dashboard", callback_data="admin_plans_refresh")
    builder.adjust(1)
    return builder.as_markup()

def get_plan_edit_fields_keyboard(plan) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🏷️ Edit Name", callback_data=f"plan_field_name_{plan.id}")
    builder.button(text="💰 Edit Price", callback_data=f"plan_field_price_{plan.id}")
    
    val_type = getattr(plan, "plan_type", None)
    if val_type and getattr(val_type, "value", str(val_type)) == "credits":
        builder.button(text=f"🪙 Edit Credits ({plan.credits})", callback_data=f"plan_field_val_{plan.id}")
    else:
        builder.button(text=f"👑 Edit Days ({plan.days})", callback_data=f"plan_field_val_{plan.id}")

    builder.button(text="🔙 Back to Plans", callback_data="admin_plan_list_edit")
    builder.adjust(2, 1, 1)
    return builder.as_markup()

def get_plan_delete_confirm_keyboard(plan_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Yes, Delete", callback_data=f"do_delete_plan_{plan_id}")
    builder.button(text="❌ Cancel", callback_data="admin_plan_list_delete")
    builder.adjust(2)
    return builder.as_markup()

def get_search_type_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📱 Phone Number", callback_data="search_type_phone")
    builder.button(text="🪪 Aadhaar", callback_data="search_type_aadhar")
    builder.adjust(2)
    return builder.as_markup()

def get_force_sub_keyboard(missing_channels: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for ch in missing_channels:
        builder.button(text=f"📢 Join {ch.title}", url=ch.invite_link)
    builder.button(text="🔄 Check Again / Verify", callback_data="verify_sub")
    builder.adjust(1)
    return builder.as_markup()

def get_admin_channels_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Add Channel", callback_data="admin_ch_add")
    builder.button(text="🗑️ Remove Channel", callback_data="admin_ch_del_list")
    builder.button(text="🔄 Refresh", callback_data="admin_ch_refresh")
    builder.button(text="❌ Close", callback_data="admin_ch_close")
    builder.adjust(2, 2)
    return builder.as_markup()

def get_channels_delete_keyboard(channels: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for ch in channels:
        builder.button(text=f"🗑️ {ch.title}", callback_data=f"admin_ch_del_{ch.id}")
    builder.button(text="🔙 Back to Channels", callback_data="admin_ch_refresh")
    builder.adjust(1)
    return builder.as_markup()
