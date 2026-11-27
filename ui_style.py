"""ألوان أزرار تيليغرام (Bot API 9.4+): success = أخضر، danger = أحمر، primary = أزرق.

إذا كانت نسخة pyTelegramBotAPI المثبّتة لا تعرف الحقل style نضيفه يدوياً عند التحويل إلى JSON،
وإذا كان تطبيق تيليغرام عند العميل قديماً يتجاهل اللون ويبقى الزر يعمل بشكل طبيعي.
"""
from telebot import types

SUCCESS, DANGER, PRIMARY = "success", "danger", "primary"


def styled(button, style):
    """يضيف لوناً للزر ويعيده. لا يغيّر النص ولا الـ callback."""
    if style not in (SUCCESS, DANGER, PRIMARY):
        return button
    button.style = style
    try:
        serialized = button.to_dict()
    except Exception:
        return button
    if serialized.get("style") != style:
        original = button.to_dict

        def to_dict():
            data = original()
            data["style"] = style
            return data

        button.to_dict = to_dict
    return button


def availability_button(text, callback, available):
    """أخضر إذا كان العنصر متوفراً وأحمر إذا لم يكن."""
    return styled(types.InlineKeyboardButton(text, callback_data=callback), SUCCESS if available else DANGER)


def stock_suffix(item, lang="ar"):
    """نص قصير بجانب الزر: الكمية إن كانت معروفة، أو «نفد» عند عدم التوفر."""
    if item.get("availability") == "unavailable":
        return " · نفد" if lang == "ar" else " · Out"
    count = item.get("stock_count")
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        return f" · 📦 {count if count < 1000 else '999+'}"
    return ""
