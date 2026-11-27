import re
import unicodedata

API_BRAND = "ai_api"
OTHER_BRAND = "other"
BRANDS = {
    "chatgpt": ("🤖 ChatGPT", ("chat gpt", "chatgpt", "openai", "gpt", "شات جي بي تي", "شات جي بي", "شات جيبتي", "اوبن اي اي")),
    "capcut": ("🎬 CapCut", ("capcut", "cap cut", "كاب كات", "كابكات", "كاب كت")),
    "youtube": ("▶️ YouTube", ("youtube", "يوتيوب", "يوتوب")),
    "gemini": ("✨ Gemini", ("gemini", "جيميني", "جيميناي", "جمني", "جيمناي")),
    "nordvpn": ("🛡 NordVPN", ("nordvpn", "nord vpn", "نورد في بي ان", "نورد")),
    "netflix": ("🍿 Netflix", ("netflix", "نتفليكس", "نتفلكس")),
    "microsoft": ("🪟 Microsoft", ("microsoft", "office 365", "office", "windows", "copilot", "مايكروسوفت", "مايكروسفت", "ويندوز", "اوفيس")),
    "gmail": ("📧 Gmail", ("gmail", "google mail", "جيميل", "جي ميل")),
    "grok": ("✦ Grok", ("grok", "xai", "جروك", "غروك", "كروك")),
    "duolingo": ("🦉 Duolingo", ("duolingo", "دولينجو", "ديولينجو", "دولينغو")),
    "canva": ("🎨 Canva", ("canva", "كانفا")),
    API_BRAND: ("🔑 AI API", ("openrouter", "open router", "open router credits", "اوبن راوتر")),
    "airtable": ("🗃 Airtable", ("airtable", "air table", "ايرتابل", "اير تيبل")),
    "adobe": ("🖌 Adobe", ("adobe", "photoshop", "lightroom", "premiere pro", "acrobat", "ادوبي", "فوتوشوب")),
    "appletv": ("📺 Apple TV", ("apple tv", "appletv", "ابل تي في")),
    "amazon": ("📦 Amazon", ("amazon", "prime video", "امازون", "برايم فيديو")),
    "coursera": ("🎓 Coursera", ("coursera", "كورسيرا", "كورسيرا بلس")),
    "manus": ("🖐 Manus", ("manus", "مانوس", "مانس")),
    "autodesk": ("📐 Autodesk", ("autodesk", "autocad", "revit", "اوتوديسك", "اوتوكاد", "ريفيت")),
    "zoom": ("📹 Zoom", ("zoom", "زوم")),
    "lovable": ("💜 Lovable", ("lovable", "لوفابل")),
    "supabase": ("⚡ Supabase", ("supabase", "سوبابيس", "سوبابيز")),
    "applemusic": ("🎵 Apple Music", ("apple music", "ابل ميوزك", "ابل ميوزيك")),
    "arize": ("📊 Arize", ("arize", "اريز")),
    "claude": ("🧠 Claude", ("claude", "anthropic", "كلود", "انثروبيك")),
    "perplexity": ("🔎 Perplexity", ("perplexity", "بيربلكسيتي", "بيربليكسيتي")),
    "midjourney": ("🖼 Midjourney", ("midjourney", "mid journey", "ميدجورني", "ميد جورني")),
    "spotify": ("🎧 Spotify", ("spotify", "سبوتيفاي", "سبوتيفي")),
    "disney": ("🏰 Disney+", ("disney", "ديزني")),
    "shahid": ("🍿 Shahid", ("shahid", "شاهد")),
    "xbox": ("🎮 Xbox", ("xbox", "x box", "اكس بوكس", "اكسبوكس", "game pass")),
    "steam": ("🕹 Steam", ("steam", "ستيم")),
    "playstation": ("🎮 PlayStation", ("playstation", "play station", "psn", "بلايستيشن", "بلاي ستيشن")),
    "pubg": ("🎯 PUBG", ("pubg", "ببجي")),
    "roblox": ("🎲 Roblox", ("roblox", "robux", "روبلوكس", "روبوكس")),
    "valorant": ("⚔️ Valorant", ("valorant", "فالورانت")),
    "cursor": ("💻 Cursor", ("cursor", "كيرسر", "كورسور")),
    "notion": ("📓 Notion", ("notion", "نوشن", "نوتيون")),
    "figma": ("✏️ Figma", ("figma", "فيغما", "فيجما")),
}


def normalize(value):
    value = "".join(char for char in unicodedata.normalize("NFKD", value.casefold())
                    if not unicodedata.combining(char))
    value = value.translate(str.maketrans({"ـ": "", "ى": "ي", "ة": "ه"}))
    return re.sub(r"[^\w]+|_+", " ", value).strip()


PATTERNS = {
    key: re.compile(r"\b(?:" + "|".join(re.escape(normalize(alias)) for alias in aliases) + r")\b")
    for key, (_, aliases) in BRANDS.items()
}
AI_BRANDS = {"chatgpt", "gemini", "grok", "claude", "perplexity", "midjourney"}
API_WORDS = re.compile(r"\b(?:api|a p i|اي بي اي)\b")
API_CONTEXT = re.compile(r"\b(?:ai|keys?|credits?|tokens?|reseller|مفتاح|مفاتيح|ارصده|رصيد|ذكاء)\b")


def brand_for(name):
    name = normalize(name)
    matches = [key for key, pattern in PATTERNS.items() if pattern.search(name)]
    if API_BRAND in matches or API_WORDS.search(name) and (AI_BRANDS.intersection(matches) or API_CONTEXT.search(name)):
        return API_BRAND
    return matches[0] if matches else OTHER_BRAND


def product_groups(products, primary_names=()):
    groups = {}
    by_id = {item["id"]: item["name"] for item in primary_names}
    for item in products:
        brand = item.get("shop_meta", {}).get("brand") or brand_for(item["name"])
        if brand == OTHER_BRAND and item["id"] in by_id:
            brand = brand_for(by_id[item["id"]])
        groups.setdefault(brand, []).append(item)
    return groups


def order_quantity(product):
    spec = product.get("input") or {}
    return spec.get("min_quantity", 1) if spec.get("type") == "quantity" else 1
