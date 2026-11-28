import re
from html import escape
from string import Formatter


TEXTS = {
    "en": {
        "welcome": "👋 *Welcome, {name}!*\n\n🛍 *Techo Pay — Official Digital Store*\n💰 *Your Balance:* ${bal}\n\nInstant automated digital delivery for software, keys, licenses, and accounts.\nSelect an option below:",
        "btn_cats": "🗂 Browse Categories",
        "btn_wallet": "💳 Wallet",
        "btn_purchases": "📦 My Purchases",
        "btn_refresh": "🔄 Refresh Stocks",
        "btn_profile": "👤 My Profile",
        "btn_lang": "🌐 Language",
        "btn_support": "💬 Customer Support",
        "btn_main": "🏠 Main Menu",
        "btn_all": "🛍 All Products",
        "btn_prev": "⬅️ Prev",
        "btn_next": "Next ➡️",
        "btn_buy": "🛒 Buy Now",
        "btn_vault": "📦 View My Vault",
        "cats_title": "🗂 *Categories*\nSelect a category to explore:",
        "catalog_title": "🛍 *Product Catalog* ({count} items)\nGreen = in stock · Red = out of stock. Tap a product for details:",
        "profile_text": "👤 *Customer Profile*\n\n🆔 Telegram ID: `{uid}`\n👤 Name: {name}\n💰 Current Balance: *${bal}*\n\n📊 *Purchase Summary:*\n📦 Completed Orders: {orders}\n🔑 Digital Keys Received: {keys} unit(s)\n💵 Total Spent: *${spent}*",
        "no_purchases": "📦 You have not purchased any products yet.",
        "wallet_text": "💳 *Digital Wallet & Balance*\n\n💰 *Current Balance:* ${bal}\n\n🌐 *Step 1:* Choose deposit cryptocurrency network:",
        "deposit_info": "💳 *{net}*\n\nSend your payment to this address:\n`{addr}`\n\nAfter sending, reply with the **Amount & Transaction Hash (TxID)** to notify admin.",
        "deposit_sent": "✅ Your deposit request has been sent to the admin for verification.",
        "refreshed": "✅ Stocks refreshed successfully!",
        "ask_id": "✍️ Please send the **Player ID / Account Identifier** for ({name}):",
        "ask_email": "📧 Please send the **Email Address** for ({name}):",
        "low_bal": "❌ Insufficient balance! Price: ${price}",
        "unavail": "❌ This product is currently unavailable.",
        "processing": "⏳ Processing your order...",
        "order_ok": "✅ *Order Completed!*\nProduct: {name}\nOrder ID: `{oid}`",
        "order_pending": "⏳ *Order Processing*\nOrder ID: `{oid}`\nStatus: `{status}`",
        "order_fail": "❌ Order failed ({err}). Amount refunded to your balance.",
        "choose_lang": "🌐 Choose your language:"
    },
    "ar": {
        "welcome": "👋 *أهلاً بك، {name}!*\n\n🛍 *Techo Pay — المتجر الرقمي الرسمي*\n💰 *رصيدك الحالي:* ${bal}\n\nتسليم فوري وتلقائي للبرامج، المفاتيح الرقمية، الاشتراكات، والحسابات.\nاختر من القائمة أدناه:",
        "btn_cats": "🗂 تصفح التصنيفات",
        "btn_wallet": "💳 المحفظة",
        "btn_purchases": "📦 مشترياتي",
        "btn_refresh": "🔄 تحديث المخزون",
        "btn_profile": "👤 ملفي الشخصي",
        "btn_lang": "🌐 اللغة",
        "btn_support": "💬 الدعم الفني",
        "btn_main": "🏠 القائمة الرئيسية",
        "btn_all": "🛍 جميع المنتجات",
        "btn_prev": "⬅️ السابق",
        "btn_next": "التالي ➡️",
        "btn_buy": "🛒 شراء الآن",
        "btn_vault": "📦 عرض خزنتي",
        "cats_title": "🗂 *التصنيفات*\nاختر تصنيفاً لاستعراض المنتجات:",
        "catalog_title": "🛍 *كتالوج المنتجات* ({count} منتج)\nالأخضر متوفر والأحمر نفد. اضغط على المنتج لعرض التفاصيل:",
        "profile_text": "👤 *ملف العميل*\n\n🆔 معرّف تلغرام: `{uid}`\n👤 الاسم: {name}\n💰 الرصيد الحالي: *${bal}*\n\n📊 *ملخص المشتريات:*\n📦 الطلبات المكتملة: {orders}\n🔑 الأكواد المستلمة: {keys}\n💵 إجمالي الإنفاق: *${spent}*",
        "no_purchases": "📦 لم تقم بشراء أي منتجات حتى الآن.",
        "wallet_text": "💳 *المحفظة الرقمية والرصيد*\n\n💰 *الرصيد الحالي:* ${bal}\n\n🌐 *الخطوة 1:* اختر شبكة الإيداع:",
        "deposit_info": "💳 *{net}*\n\nأرسل المبلغ إلى العنوان التالي:\n`{addr}`\n\nبعد التحويل، أرسل هنا **المبلغ ورقم العملية (TxID)** ليتم شحن رصيدك.",
        "deposit_sent": "✅ تم إرسال طلب الإيداع للإدارة للمراجعة والشحن.",
        "refreshed": "✅ تم تحديث المخزون بنجاح!",
        "ask_id": "✍️ أرسل **معرّف اللاعب / الحساب** لتفعيل ({name}):",
        "ask_email": "📧 أرسل **البريد الإلكتروني** لتفعيل ({name}):",
        "low_bal": "❌ رصيدك غير كافٍ! السعر: ${price}",
        "unavail": "❌ هذا المنتج غير متوفر حالياً.",
        "processing": "⏳ جاري تنفيذ طلبك...",
        "order_ok": "✅ *تم تنفيذ طلبك بنجاح!*\nالمنتج: {name}\nرقم الطلب: `{oid}`",
        "order_pending": "⏳ *طلبك قيد المعالجة*\nرقم الطلب: `{oid}`\nالحالة: `{status}`",
        "order_fail": "❌ تعذر تنفيذ الطلب ({err}). تم إعادة المبلغ لرصيدك.",
        "choose_lang": "🌐 اختر لغتك المفضلة:"
    },
    "vi": {
        "welcome": "👋 *Chào mừng, {name}!*\n\n🛍 *Techo Pay — Cửa hàng Kỹ thuật số*\n💰 *Số dư:* ${bal}\n\nGiao hàng tự động tức thì cho phần mềm, key và tài khoản.\nChọn một tùy chọn bên dưới:",
        "btn_cats": "🗂 Danh mục",
        "btn_wallet": "💳 Ví tiền",
        "btn_purchases": "📦 Đơn hàng",
        "btn_refresh": "🔄 Làm mới kho",
        "btn_profile": "👤 Hồ sơ",
        "btn_lang": "🌐 Ngôn ngữ",
        "btn_support": "💬 Hỗ trợ",
        "btn_main": "🏠 Menu chính",
        "btn_all": "🛍 Tất cả sản phẩm",
        "btn_prev": "⬅️ Trước",
        "btn_next": "Tiếp ➡️",
        "btn_buy": "🛒 Mua ngay",
        "btn_vault": "📦 Kho của tôi",
        "cats_title": "🗂 *Danh mục*\nChọn danh mục để khám phá:",
        "catalog_title": "🛍 *Danh mục sản phẩm* ({count} có sẵn)\nNhấn vào sản phẩm để mua:",
        "profile_text": "👤 *Hồ sơ khách hàng*\n\n🆔 ID: `{uid}`\n👤 Tên: {name}\n💰 Số dư: *${bal}*\n\n📊 *Tóm tắt:*\n📦 Đơn hoàn thành: {orders}\n🔑 Key đã nhận: {keys}\n💵 Tổng chi tiêu: *${spent}*",
        "no_purchases": "📦 Bạn chưa mua sản phẩm nào.",
        "wallet_text": "💳 *Ví Kỹ thuật số*\n\n💰 *Số dư:* ${bal}\n\n🌐 *Bước 1:* Chọn mạng nạp tiền:",
        "deposit_info": "💳 *{net}*\n\nGửi tiền đến địa chỉ:\n`{addr}`\n\nSau khi gửi, hãy nhắn **Số tiền & TxID** tại đây.",
        "deposit_sent": "✅ Yêu cầu nạp tiền đã được gửi cho quản trị viên.",
        "refreshed": "✅ Đã làm mới kho hàng!",
        "ask_id": "✍️ Vui lòng gửi **ID người chơi / Tài khoản** cho ({name}):",
        "ask_email": "📧 Vui lòng gửi **Email** cho ({name}):",
        "low_bal": "❌ Số dư không đủ! Giá: ${price}",
        "unavail": "❌ Sản phẩm hiện không có sẵn.",
        "processing": "⏳ Đang xử lý đơn hàng...",
        "order_ok": "✅ *Đơn hàng hoàn tất!*\nSản phẩm: {name}\nMã đơn: `{oid}`",
        "order_pending": "⏳ *Đang xử lý*\nMã đơn: `{oid}`\nTrạng thái: `{status}`",
        "order_fail": "❌ Lỗi đơn hàng ({err}). Đã hoàn tiền.",
        "choose_lang": "🌐 Chọn ngôn ngữ:"
    },
    "hi": {
        "welcome": "👋 *स्वागत है, {name}!*\n\n🛍 *Techo Pay — आधिकारिक डिजिटल स्टोर*\n💰 *आपका बैलेंस:* ${bal}\n\nसॉफ़्टवेयर, कीज़ और अकाउंट्स की तुरंत डिलीवरी।\nनीचे एक विकल्प चुनें:",
        "btn_cats": "🗂 श्रेणियाँ",
        "btn_wallet": "💳 वॉलेट",
        "btn_purchases": "📦 मेरी खरीदारी",
        "btn_refresh": "🔄 स्टॉक रिफ्रेश",
        "btn_profile": "👤 मेरी प्रोफ़ाइल",
        "btn_lang": "🌐 भाषा",
        "btn_support": "💬 सहायता",
        "btn_main": "🏠 मुख्य मेनू",
        "btn_all": "🛍 सभी उत्पाद",
        "btn_prev": "⬅️ पिछला",
        "btn_next": "अगला ➡️",
        "btn_buy": "🛒 अभी खरीदें",
        "btn_vault": "📦 मेरा वॉल्ट",
        "cats_title": "🗂 *श्रेणियाँ*\nदेखने के लिए एक श्रेणी चुनें:",
        "catalog_title": "🛍 *उत्पाद कैटलॉग* ({count} उपलब्ध)\nखरीदने के लिए उत्पाद पर टैप करें:",
        "profile_text": "👤 *ग्राहक प्रोफ़ाइल*\n\n🆔 ID: `{uid}`\n👤 नाम: {name}\n💰 बैलेंस: *${bal}*\n\n📊 *सारांश:*\n📦 पूर्ण ऑर्डर: {orders}\n🔑 प्राप्त कीज़: {keys}\n💵 कुल खर्च: *${spent}*",
        "no_purchases": "📦 आपने अभी तक कोई उत्पाद नहीं खरीदा है।",
        "wallet_text": "💳 *डिजिटल वॉलेट*\n\n💰 *बैलेंस:* ${bal}\n\n🌐 *चरण 1:* जमा नेटवर्क चुनें:",
        "deposit_info": "💳 *{net}*\n\nइस पते पर भुगतान भेजें:\n`{addr}`\n\nभेजने के बाद, **राशि और TxID** यहाँ भेजें।",
        "deposit_sent": "✅ आपका जमा अनुरोध एडमिन को भेज दिया गया है।",
        "refreshed": "✅ स्टॉक सफलतापूर्वक रिफ्रेश हो गया!",
        "ask_id": "✍️ कृपया ({name}) के लिए **प्लेयर आईडी / अकाउंट** भेजें:",
        "ask_email": "📧 कृपया ({name}) के लिए **ईमेल पता** भेजें:",
        "low_bal": "❌ अपर्याप्त बैलेंस! मूल्य: ${price}",
        "unavail": "❌ यह उत्पाद अभी उपलब्ध नहीं है।",
        "processing": "⏳ आपका ऑर्डर प्रोसेस हो रहा है...",
        "order_ok": "✅ *ऑर्डर पूरा हुआ!*\nउत्पाद: {name}\nऑर्डर आईडी: `{oid}`",
        "order_pending": "⏳ *ऑर्डर प्रोसेसिंग में है*\nऑर्डर आईडी: `{oid}`\nस्थिति: `{status}`",
        "order_fail": "❌ ऑर्डर विफल ({err})। राशि वापस कर दी गई।",
        "choose_lang": "🌐 अपनी भाषा चुनें:"
    },
    "fr": {
        "welcome": "👋 *Bienvenue, {name}!*\n\n🛍 *Techo Pay — Boutique Numérique*\n💰 *Votre Solde:* ${bal}\n\nLivraison numérique instantanée pour logiciels, clés et comptes.\nSélectionnez une option:",
        "btn_cats": "🗂 Catégories",
        "btn_wallet": "💳 Portefeuille",
        "btn_purchases": "📦 Mes Achats",
        "btn_refresh": "🔄 Actualiser",
        "btn_profile": "👤 Mon Profil",
        "btn_lang": "🌐 Langue",
        "btn_support": "💬 Support",
        "btn_main": "🏠 Menu Principal",
        "btn_all": "🛍 Tous les produits",
        "btn_prev": "⬅️ Préc.",
        "btn_next": "Suiv. ➡️",
        "btn_buy": "🛒 Acheter",
        "btn_vault": "📦 Mon Coffre",
        "cats_title": "🗂 *Catégories*\nChoisissez une catégorie:",
        "catalog_title": "🛍 *Catalogue* ({count} disponibles)\nAppuyez sur un produit pour acheter:",
        "profile_text": "👤 *Profil Client*\n\n🆔 ID: `{uid}`\n👤 Nom: {name}\n💰 Solde: *${bal}*\n\n📊 *Résumé:*\n📦 Commandes: {orders}\n🔑 Clés reçues: {keys}\n💵 Total dépensé: *${spent}*",
        "no_purchases": "📦 Vous n'avez encore rien acheté.",
        "wallet_text": "💳 *Portefeuille Numérique*\n\n💰 *Solde:* ${bal}\n\n🌐 *Étape 1:* Choisissez le réseau de dépôt:",
        "deposit_info": "💳 *{net}*\n\nEnvoyez le paiement à cette adresse:\n`{addr}`\n\nEnsuite, envoyez le **Montant et TxID** ici.",
        "deposit_sent": "✅ Votre demande de dépôt a été envoyée à l'admin.",
        "refreshed": "✅ Stocks actualisés avec succès!",
        "ask_id": "✍️ Veuillez envoyer l'**ID / Compte** pour ({name}):",
        "ask_email": "📧 Veuillez envoyer l'**Adresse E-mail** pour ({name}):",
        "low_bal": "❌ Solde insuffisant! Prix: ${price}",
        "unavail": "❌ Ce produit est indisponible.",
        "processing": "⏳ Traitement de votre commande...",
        "order_ok": "✅ *Commande terminée!*\nProduit: {name}\nID: `{oid}`",
        "order_pending": "⏳ *Commande en cours*\nID: `{oid}`\nStatut: `{status}`",
        "order_fail": "❌ Échec ({err}). Montant remboursé.",
        "choose_lang": "🌐 Choisissez votre langue:"
    }
}


TEXTS["en"].update({
    "welcome": "👋 Welcome to *Techo Pay*!\nChoose an option below:",
    "force_sub": "⚠️ Please join our channel to unlock the bot and get stock updates.\n\n📢 {channel}",
    "btn_join": "📢 Join Channel",
    "btn_check_sub": "✅ Yes, I Subscribed",
    "not_subbed": "❌ Join the channel first, then try again.",
    "btn_wallet": "💳 Wallet & Deposit",
    "wallet_text": "💳 *Your Wallet*\n💰 Current balance: *${bal:.2f}*\n\nSelect a deposit method:",
    "profile_text": TEXTS["en"]["profile_text"] + "\n👥 Referrals: *{refs}*",
    "btn_referral": "🔗 My Referral Link",
    "referral_text": "🔗 *Your Referral Link*\n\n`{link}`\n\nShare it with friends! Rewards and discounts are in development.",
    "deposit_info": ("💳 *Deposit via {net}*\n\n📍 Address:\n`{addr}`\n\n"
                     "1️⃣ Use only the *{net}* network.\n2️⃣ Minimum deposit: *${min_amount:.2f}*.\n"
                     "3️⃣ Make sure the net amount arrives after fees.\n"
                     "4️⃣ Send the amount and transaction ID (TxID) here for admin review."),
    "btn_back": "⬅️ Back",
    "category_games": "🎮 Games",
    "category_ai": "🤖 AI Tools",
    "category_design": "🎬 Design & Editing",
    "category_streaming": "🍿 Streaming",
    "category_other": "📦 Other Services",
    "buy_confirm": "Confirm your purchase of *{name}* for *${price}*.",
    "confirm": "✅ Confirm Purchase",
    "invalid_input": "❌ Check the value and reply again.",
    "price": "Price",
    "order_failed_state": "❌ Order failed ({err}). The amount was refunded to your balance.",
})
TEXTS["ar"].update({
    "welcome": "👋 أهلاً بك في *Techo Pay*!\nاختر من القائمة أدناه للبدء:",
    "force_sub": "⚠️ اشترك في قناة البوت أولاً لتفعيله ومتابعة العروض والمخزون الجديد.\n\n📢 {channel}",
    "btn_join": "📢 اشترك في القناة",
    "btn_check_sub": "✅ نعم، لقد اشتركت",
    "not_subbed": "❌ لم يتم العثور على اشتراكك. اشترك في القناة ثم حاول مجدداً.",
    "btn_wallet": "💳 المحفظة والشحن",
    "wallet_text": "💳 *محفظتك*\n💰 الرصيد الحالي: *${bal:.2f}*\n\nاختر طريقة الشحن:",
    "profile_text": TEXTS["ar"]["profile_text"] + "\n👥 عدد المدعوين: *{refs}*",
    "btn_referral": "🔗 رابط الإحالة الخاص بي",
    "referral_text": "🔗 *رابط الإحالة الخاص بك*\n\n`{link}`\n\nشاركه مع أصدقائك! نظام المكافآت والخصومات قيد التطوير.",
    "deposit_info": ("💳 *تعليمات الشحن عبر {net}*\n\n📍 العنوان:\n`{addr}`\n\n"
                     "1️⃣ أرسل فقط عبر شبكة *{net}*.\n2️⃣ الحد الأدنى للإيداع *${min_amount:.2f}*.\n"
                     "3️⃣ تأكد من وصول المبلغ الصافي بعد خصم العمولة.\n"
                     "4️⃣ أرسل المبلغ ورقم العملية (TxID) هنا لمراجعة الإدارة."),
    "btn_back": "🔙 رجوع",
    "category_games": "🎮 الألعاب",
    "category_ai": "🤖 أدوات الذكاء الاصطناعي",
    "category_design": "🎬 التصميم والمونتاج",
    "category_streaming": "🍿 البث والترفيه",
    "category_other": "📦 خدمات أخرى",
    "buy_confirm": "أكّد شراء *{name}* بسعر *${price}*.",
    "confirm": "✅ تأكيد الشراء",
    "invalid_input": "❌ تحقق مما أرسلته وحاول مجدداً.",
    "price": "السعر",
    "order_failed_state": "❌ تعذر تنفيذ الطلب ({err}). تم إعادة المبلغ لرصيدك.",
})


TEXTS["ar"].update({
    "cv_app_button": "📝 تعبئة عبر Mini App",
    "cv_app_button_short": "📝 افتح نموذج السيرة",
    "cv_file_caption": "📎 سيرتك الذاتية جاهزة · طلب #{order_id}",
    "cv_app_launch": "تشغيل Mini App",
    "cv_app_paid": "تم الدفع، اضغط تشغيل Mini App لتعبئة بيانات السيرة",
    "cv_app_paid_receipt": "تم الدفع للطلب #{order_id}.",
    "cv_app_open": "📝 *نموذج السيرة الذاتية · طلب #{order_id}*\nاضغط الزر أدناه لفتح النموذج، واختر لغة السيرة ثم عبّئ بياناتك. الرابط صالح 30 دقيقة ويُستخدم مرة واحدة. وإن واجهتك مشكلة يمكنك التواصل مع الدعم وإرسال بياناتك.",
    "cv_app_saved": "✅ استلمنا بيانات سيرتك الذاتية للطلب *#{order_id}*. سنراجعها ونتواصل معك قريباً.",
    "cv_app_done": "✅ وصلتنا بياناتك عبر النموذج وهي قيد المراجعة.",
    "cv_app_unavailable": "النموذج غير متاح حالياً. تواصل مع الدعم وأرسل بياناتك مع رقم طلبك.",
    "cv_app_err_order": "تعذّر فتح النموذج لهذا الطلب. تأكد أنه مدفوع وبانتظار المعلومات ولم تُرسل بياناته سابقاً.",
    "cv_app_err_token": "رابط النموذج غير صالح. افتح النموذج من بطاقة الطلب من جديد.",
    "cv_app_err_used": "هذا الرابط استُخدم سابقاً أو أُرسلت بيانات هذا الطلب من قبل، فلا يمكن الإرسال مرة ثانية.",
    "cv_app_err_expired": "انتهت صلاحية الرابط. افتح النموذج من بطاقة الطلب من جديد.",
    "cv_app_err_invalid": "❌ تعذّر اعتماد البيانات. راجع الحقل: {field}",
})
TEXTS["en"].update({
    "cv_app_button": "📝 Fill in via Mini App",
    "cv_app_button_short": "📝 Open CV form",
    "cv_file_caption": "📎 Your CV is ready · order #{order_id}",
    "cv_app_launch": "Launch Mini App",
    "cv_app_paid": "Payment confirmed. Tap Launch Mini App to fill in your CV details.",
    "cv_app_paid_receipt": "Payment confirmed for order #{order_id}.",
    "cv_app_open": "📝 *CV form · order #{order_id}*\nTap the button below to open the form, pick your CV language and fill in your details. The link is valid for 30 minutes and works once. If you have trouble, contact support and send your details.",
    "cv_app_saved": "✅ We received your CV details for order *#{order_id}*. We will review them and get back to you soon.",
    "cv_app_done": "✅ Your details were received through the form and are under review.",
    "cv_app_unavailable": "The form is not available right now. Contact support and send your details with your order number.",
    "cv_app_err_order": "The form can't be opened for this order. Make sure it is paid, awaiting details and has no earlier submission.",
    "cv_app_err_token": "The form link is invalid. Open the form again from the order card.",
    "cv_app_err_used": "This link was already used, or this order already has submitted details, so it can't be sent again.",
    "cv_app_err_expired": "The link has expired. Open the form again from the order card.",
    "cv_app_err_invalid": "❌ We couldn't accept the data. Check the field: {field}",
})


def t(lang, key, **kwargs):
    template = TEXTS.get(lang, {}).get(key, TEXTS["en"].get(key, key))
    template = escape(template, quote=False)
    template = re.sub(r"\`([^`]+)`", r"<code>\1</code>", template)
    template = re.sub(r"\*\*([^*]+)\*\*|\*([^*]+)\*",
                      lambda match: "<b>" + (match[1] or match[2]) + "</b>", template)
    return "".join(
        literal + (escape(format(kwargs[field], spec), quote=False) if field else "")
        for literal, field, spec, _ in Formatter().parse(template)
    )


TEXTS["ar"].update({
    "cv_button": "📄 سيرة ذاتية ATS · $5.00",
    "cv_name": "إنشاء سيرة ذاتية متوافقة مع ATS",
    "cv_offer": "📄 *سيرة ذاتية متوافقة مع ATS*\nالسعر: *$5.00*.\n\nعند التأكيد يُخصم المبلغ من رصيد محفظتك لإنشاء الطلب. بعدها تواصل مع الدعم وأرسل رقم الطلب وقدّم معلوماتك كاملة.",
    "cv_confirm": "✅ تأكيد الطلب · $5.00",
    "cv_details": "📄 *طلب السيرة الذاتية #{order_id}*\nالحالة: *{status}*\n\nاضغط «تواصل مع الدعم» وأرسل رقم الطلب *#{order_id}* مع بياناتك الكاملة لإعداد السيرة الذاتية.",
    "cv_support": "💬 تواصل مع الدعم",
    "cv_expired": "انتهت هذه المحاولة. افتح خدمة السيرة الذاتية من القائمة للمحاولة من جديد.",
    "cv_status_awaiting_details": "بانتظار المعلومات",
    "cv_status_in_progress": "قيد التنفيذ",
    "cv_status_completed": "مكتمل",
    "cv_status_cancelled": "ملغى",
    "cv_notice_in_progress": "📄 بدأ العمل على طلب السيرة الذاتية #{order_id}.",
    "cv_notice_completed": "✅ اكتمل طلب السيرة الذاتية #{order_id}. تواصل مع الدعم لمتابعة التسليم.",
    "cv_notice_cancelled": "↩️ أُلغي طلب السيرة الذاتية #{order_id}، وأُعيد مبلغ $5.00 إلى محفظتك.",
    "price_paused": "الخدمة موقوفة مؤقتاً بسبب تغيّر تكلفة المورد.",
    "price_updated": "تغيّر تسعير هذه الخدمة. افتح الخدمة من جديد لعرض السعر قبل الشراء.",
})

TEXTS["en"].update({
    "cv_button": "📄 ATS-friendly CV · $5.00",
    "cv_name": "ATS-friendly CV writing",
    "cv_offer": "📄 *ATS-friendly CV writing*\nPrice: *$5.00*.\n\nConfirm to pay from your wallet and place the order. Then contact support with your order number and full details.",
    "cv_confirm": "✅ Confirm order · $5.00",
    "cv_details": "📄 *CV order #{order_id}*\nStatus: *{status}*\n\nTap Contact support and share order *#{order_id}* along with the details for your CV.",
    "cv_support": "💬 Contact support",
    "cv_expired": "This request expired. Open the CV service from the menu and try again.",
    "cv_status_awaiting_details": "Awaiting details",
    "cv_status_in_progress": "In progress",
    "cv_status_completed": "Completed",
    "cv_status_cancelled": "Cancelled",
    "cv_notice_in_progress": "📄 Work has started on CV order #{order_id}.",
    "cv_notice_completed": "✅ CV order #{order_id} is complete. Contact support to arrange delivery.",
    "cv_notice_cancelled": "↩️ CV order #{order_id} was cancelled and $5.00 was refunded to your wallet.",
    "price_paused": "This service is temporarily paused because the supplier cost has changed.",
    "price_updated": "This service's price has changed. Reopen the product to review the price before buying.",
})


TEXTS["ar"].update({
    "btn_cats": "🛍 المنتجات",
    "brands_title": "🛍 *المنتجات*\nاختَر العلامة لتشوف الاشتراكات والخدمات المتاحة.",
    "brands_api": "🔑 مفاتيح وAPI للذكاء الاصطناعي",
    "brands_other": "📦 علامات وخدمات أخرى",
    "browse_types": "🗂 حسب نوع الخدمة",
    "back_brands": "⬅️ العلامات",
    "brand_no_products": "لا توجد منتجات متاحة حالياً في هذا القسم.",
})
TEXTS["en"].update({
    "btn_cats": "🛍 Products",
    "brands_title": "🛍 *Products*\nChoose a brand to see its available products.",
    "brands_api": "🔑 AI API & keys",
    "brands_other": "📦 Other brands & services",
    "browse_types": "🗂 Browse by service type",
    "back_brands": "⬅️ Brands",
    "brand_no_products": "There are currently no available products in this section.",
})


SHOP_TEXT = {
    "search": ("🔎 البحث", "🔎 Search"),
    "search_prompt": ("للبحث، اكتب اسم المنتج أو العلامة التجارية بالعربية أو الإنجليزية.", "Send a product or brand name in Arabic or English."),
    "missing": ("لم نجد منتجات تطابق بحثك. جرّب اسم العلامة التجارية أو كلمة أقصر.", "No matching products. Try another search."),
    "membership_error": ("تعذّر التحقق من الاشتراك حالياً. يرجى المحاولة بعد قليل.", "We couldn't check your membership. Please try again shortly."),
    "unit_price": ("السعر للوحدة قبل مراجعة الطلب", "Unit price before checkout review"),
    "checkout_off": ("الدفع لهذه الباقة غير متاح حالياً. يمكنك التواصل مع الدعم للاستفسار.", "Checkout for this plan is currently unavailable. Contact support for help."),
    "changed": ("تغيّر السعر أو حالة التوفّر منذ اختيارك. يرجى مراجعة تفاصيل الطلب قبل الدفع.", "Price or availability changed. Review the details before paying."),
    "review": ("راجع طلبك", "Review your order"),
    "qty": ("الكمية", "Quantity"),
    "target": ("الحساب المستهدف", "Activation account"),
    "price": ("السعر النهائي", "Final price"),
    "pay": ("⭐ إصدار فاتورة", "⭐ Create invoice"),
    "edit": ("✏️ تعديل البيانات", "✏️ Edit details"),
    "warn": ("راجع الباقة والسعر ومعرّف الحساب. ادفع من فاتورة البوت الرسمية، ولا تشارك كلمة المرور أو رمز التحقق.",
             "Review your plan, price and account ID. Pay via the bot's official invoice. Never share a password or verification code."),
    "wait": ("إذا ظهر تأكيد الدفع في تيليغرام، يرجى عدم الدفع مجدداً. تابع حالة طلبك أو أرسل رقمه للدعم.",
             "If Telegram shows your payment went through, don't pay again. Check the order or contact support with its number."),
    "legacy": ("رصيدك السابق: ${balance:.2f}\nللمراجعة أو التسوية تواصل مع الدعم.",
               "Previous wallet balance: ${balance:.2f}\nContact support to review or reconcile it."),
    "cv_intro": ("نُعدّ لك سيرة ذاتية متوافقة مع أنظمة تتبّع المتقدّمين (ATS). بعد الدفع، عبّئ بياناتك من نموذج السيرة.",
                 "ATS-friendly CV writing. After payment, submit your details in the CV form."),
    "support": ("💬 الدعم لهذا الطلب", "💬 Order support"),
    "support_prompt": ("يرجى وصف المشكلة باختصار، وتجنّب مشاركة كلمات المرور أو رموز التحقق.",
                       "Describe the issue without sending passwords or verification codes."),
    "support_saved": ("استلمنا طلب الدعم. يمكنك متابعة الرد من صفحة طلبك.", "Support request saved. Check your order for a reply."),
    "wallet_pay": ("💳 شراء من المحفظة", "💳 Pay from wallet"),
    "wallet_low": ("رصيد المحفظة أقل من قيمة الطلب. يمكنك شحن الرصيد ثم العودة إلى صفحة المنتج.", "Your balance is too low. Top up and return to the product."),
    "stars_orders": ("📦 طلباتي", "📦 My orders"),
    "old_orders": ("📁 الطلبات السابقة", "📁 Previous orders"),
    "paid_notice": ("تم تأكيد الدفع للطلب #{id}. تابع الخطوة التالية من طلباتك.",
                    "Payment confirmed for order #{id}. See your orders for the next step."),
    "detail_missing": ("التفاصيل غير المنشورة تحتاج تأكيداً من الدعم.", "Contact support to confirm any details not listed here."),
}
SHOP_STATES = {
    "invoice": ("بانتظار الدفع", "Awaiting payment"),
    "paid": ("مدفوع، قيد التنفيذ", "Paid, awaiting fulfillment"),
    "sending": ("مدفوع، قيد الإرسال للمورد", "Paid, submitting to supplier"),
    "processing": ("قيد التنفيذ عند المورد", "With the supplier"),
    "review": ("بحاجة لمراجعة", "Under review"),
    "manual": ("قيد المعالجة اليدوية", "In manual review"),
    "partial": ("تسليم جزئي، قيد المراجعة", "Partially delivered, under review"),
    "partial_closed": ("تمت مراجعة التسليم الجزئي", "Partial delivery reviewed"),
    "failed": ("تعذّر التنفيذ، تواصل مع الدعم", "Fulfillment failed, contact support"),
    "delivered": ("تم التسليم", "Delivered"),
    "cv_details": ("بانتظار معلومات السيرة", "Awaiting CV details"),
    "cv_work": ("السيرة قيد الإعداد", "CV in progress"),
    "refund_requested": ("طلب الاسترداد مسجّل", "Refund requested"),
    "refunding": ("الاسترداد قيد التنفيذ", "Refund in progress"),
    "refund_unknown": ("الاسترداد بحاجة للتحقق", "Refund awaiting confirmation"),
    "refunded": ("تم الاسترداد", "Refunded"),
}


UI_MESSAGES = {
    "main_coupon_referrals": (
        '🎁 الكوبونات والإحالة',
        '🎁 Coupons & referrals',
    ),
    "main_coupon_selected": (
        'سيظهر الخصم في مراجعة الطلب المؤهل.',
        'Discount shown on eligible orders.',
    ),
    "main_design_studio": (
        '🎨 صمّملي',
        '🎨 Design studio',
    ),
    "main_enter_coupon": (
        'إدخال كوبون',
        'Enter coupon',
    ),
    "main_favorites": (
        '⭐ المفضلة',
        '⭐ Favorites',
    ),
    "main_first_order_reward": (
        'مكافأة أول طلب مسلّم: ',
        'First delivered order reward: ',
    ),
    "main_help_faq": (
        '❓ الأسئلة والدعم',
        '❓ Help & FAQ',
    ),
    "main_invalid_coupon": (
        'الكوبون غير متاح.',
        'Coupon unavailable.',
    ),
    "main_payment_support": (
        'افتح الطلب واضغط زر الدعم. للأرصدة القديمة تواصل مع الدعم مباشرة.',
        'Open the order and select Order support. For previous balances, contact support directly.',
    ),
    "main_plan_finder": (
        '🤖 مساعد اختيار الباقة',
        '🤖 Plan finder',
    ),
    "main_today_offers": (
        '🔥 عروض اليوم',
        "🔥 Today's offers",
    ),
    "main_unavailable_products": (
        'باقات بانتظار التوفر',
        'Out of stock',
    ),
    "main_view_delivery": (
        'عرض التسليم',
        'View delivery',
    ),
    "shop_ui_add_funds": (
        'شحن الرصيد',
        'Add funds',
    ),
    "shop_ui_confirm_safely": (
        'راجع المبلغ والحساب قبل التأكيد. يُرجى عدم مشاركة كلمة المرور أو رمز التحقق.',
        'Review the price and account before confirming. Never send passwords or verification codes.',
    ),
    "shop_ui_delivery_method": (
        'طريقة الاستلام',
        'Delivery method',
    ),
    "shop_ui_full_delivery": (
        'كل التفاصيل',
        'Full delivery',
    ),
    "shop_ui_invoice_expired": (
        '\nانتهت صلاحية الفاتورة.',
        '\nInvoice expired.',
    ),
    "shop_ui_quantity_delivered": (
        'الكمية المسلّمة',
        'Quantity delivered',
    ),
    "shop_ui_refresh_status": (
        'تحديث الحالة',
        'Refresh status',
    ),
    "wallet_ui_add_funds": (
        '➕ شحن الرصيد',
        '➕ Add funds',
    ),
    "wallet_ui_amount_step": (
        '💰 <b>الخطوة ٢ من ٣ · تحديد مبلغ الشحن</b>\n\nوسيلة الدفع: <b>{method}</b>\n\nيرجى كتابة المبلغ الذي ترغب في شحنه بـ <b>{currency_display}</b>. سنعرض لك بيانات التحويل والمبلغ الذي سيُضاف إلى محفظتك في الخطوة التالية.',
        '💰 <b>Step 2 of 3 · Enter the top-up amount</b>\n\nPayment method: <b>{method}</b>\n\nPlease enter the amount in <b>{currency}</b>. We’ll show the transfer details and wallet credit on the next step.',
    ),
    "wallet_ui_back_methods": (
        '⬅️ وسائل الشحن',
        '⬅️ Payment methods',
    ),
    "wallet_ui_back_requests": (
        '⬅️ الطلبات',
        '⬅️ Requests',
    ),
    "wallet_ui_back_wallet": (
        '⬅️ المحفظة',
        '⬅️ Wallet',
    ),
    "wallet_ui_copy_destination": (
        '📋 نسخ العنوان',
        '📋 Copy destination',
    ),
    "wallet_ui_destination_changed": (
        'تغيّرت إعدادات الاستلام. اختر وسيلة الشحن من جديد.',
        'Payment details changed. Select the method again.',
    ),
    "wallet_ui_duplicate_reference": (
        'مرجع التحويل مستخدم بطلب سابق. يرجى مراجعته في الإيصال أو التواصل مع الدعم.',
        'This reference is already in use. Check it or contact support.',
    ),
    "wallet_ui_empty_topups": (
        'لم تُرسل أي طلبات شحن بعد.',
        'No top-up requests.',
    ),
    "wallet_ui_invalid_amount": (
        'يرجى إدخال مبلغ بالأرقام فقط، على أن يعادل 0.01 USD على الأقل.',
        'Send a valid amount worth at least 0.01 USD.',
    ),
    "wallet_ui_invalid_sham": (
        'رقم عملية شام كاش يتكوّن من 9 أرقام. انسخه من الإيصال وأرسله هنا.',
        'The Sham Cash reference has 9 digits. Copy it from your receipt and send it here.',
    ),
    "wallet_ui_invalid_syriatel": (
        'رقم عملية سيرياتيل كاش يتكوّن من 12 رقماً ويبدأ بـ <code>600</code>.',
        'The Syriatel Cash reference has 12 digits and starts with <code>600</code>.',
    ),
    "wallet_ui_invalid_txid": (
        'أرسل معرّف معاملة BEP20 من إيصال السحب. يبدأ بـ <code>0x</code> ويتكوّن من 66 محرفاً.',
        'Send the BEP20 transaction hash from your receipt. It starts with <code>0x</code> and is 66 characters long.',
    ),
    "wallet_ui_main_menu": (
        '⬅️ الرئيسية',
        '⬅️ Main menu',
    ),
    "wallet_ui_manual_disabled": (
        'شحن المحفظة اليدوي متوقف حالياً.',
        'Manual wallet top-ups are currently disabled.',
    ),
    "wallet_ui_method_step": (
        '💳 <b>الخطوة ١ من ٣ · اختيار وسيلة الشحن</b>\n\nاختر وسيلة الدفع المناسبة لك للمتابعة.',
        '💳 <b>Step 1 of 3 · Choose a top-up method</b>\n\nSelect a payment method to continue.',
    ),
    "wallet_ui_missing_rate": (
        'الشحن بهذه الوسيلة بانتظار تحديد سعر الصرف. يمكنك اختيار وسيلة أخرى الآن.',
        'This SYP method is awaiting an approved exchange rate.',
    ),
    "wallet_ui_net_warning": (
        '\n\n<blockquote>⚠️ <b>استخدم شبكة BNB Smart Chain (BEP20) فقط.</b> اختيار شبكة أخرى قد يؤدي إلى فقدان الأموال. تأكد قبل الإرسال من أن صافي المبلغ المستلم بعد الرسوم يطابق تماماً المبلغ المسجّل في الطلب. يُعلَّق الشحن إذا اختلف المبلغ إلى أن تراجعه الإدارة.</blockquote>',
        '\n\n<blockquote>⚠️ <b>Use BNB Smart Chain (BEP20) only.</b> Choosing another network may result in loss of funds. Check that the net amount received after fees exactly matches your request. A mismatch puts the credit on hold for review.</blockquote>',
    ),
    "wallet_ui_payment_methods": (
        'وسائل الشحن',
        'Payment methods',
    ),
    "wallet_ui_rate_changed": (
        'تحدّث سعر الصرف. يرجى اختيار وسيلة الشحن مرة أخرى لعرض المبلغ وفق السعر الجديد.',
        'The rate changed. Select the method again.',
    ),
    "wallet_ui_rejection_reason": (
        '\nسبب الرفض: {reason}',
        '\nReason for rejection: {reason}',
    ),
    "wallet_ui_request_list": (
        'طلبات الشحن',
        'Top-up requests',
    ),
    "wallet_ui_request_rate": (
        '\n\n💱 <b>سعر الصرف لهذا الطلب</b>',
        '\n\n💱 <b>Exchange rate for this request</b>',
    ),
    "wallet_ui_request_summary": (
        '💳 <b>طلب شحن #{id}</b>\n\nالوسيلة: {method}\nمبلغ التحويل: <b>{amount} {currency}</b>\nقيمة الشحن: <b>{credit:.2f} USD</b>\nمرجع التحويل: <code>{reference}</code>\n<blockquote>📌 الحالة: <b>{state}</b></blockquote>',
        '💳 <b>Top-up request #{id}</b>\n\nMethod: {method}\nTransfer amount: <b>{amount} {currency}</b>\nWallet credit: <b>{credit:.2f} USD</b>\nTransfer reference: <code>{reference}</code>\n<blockquote>📌 Status: <b>{state}</b></blockquote>',
    ),
    "wallet_ui_topup_received": (
        '✅ <b>استلمنا طلب الشحن #{id}</b>\n\nطلبك قيد المراجعة. سيُضاف الرصيد بعد التحقق من التحويل واعتماد الطلب. يمكنك متابعة الحالة أدناه.',
        'Top-up #{id} was received. Credit follows admin review.',
    ),
    "wallet_ui_topup_requests": (
        '📄 طلبات الشحن',
        '📄 Top-up requests',
    ),
    "wallet_ui_transfer_step": (
        '🧾 <b>الخطوة ٣ من ٣ · التحويل وإرسال المرجع</b>\n\n1️⃣ حوّل <b>{value} {currency}</b> عبر <b>{method}</b> إلى وجهة الاستلام أدناه.\n<code>{destination}</code>\n\n2️⃣ بعد إتمام التحويل، أرسل <b>{ref}</b> من الإيصال في هذه المحادثة.\n\n💰 <b>الرصيد المضاف بعد الموافقة</b>\n<blockquote>{credit:.2f} USD</blockquote>',
        '🧾 <b>Step 3 of 3 · Transfer and submit the reference</b>\n\n1️⃣ Send <b>{value} {currency}</b> via <b>{method}</b> to the destination below.\n<code>{destination}</code>\n\n2️⃣ After you transfer, send the <b>{ref}</b> from your receipt in this chat.\n\n💰 <b>Credit once approved</b>\n<blockquote>{credit:.2f} USD</blockquote>',
    ),
    "wallet_ui_txid_help": (
        '\n\nعند السحب على الشبكة، أرسل معرّف المعاملة (TxID). يبدأ بـ <code>0x</code> ويتكوّن من 66 محرفاً.',
        '\n\nFor an on-chain withdrawal, send the transaction hash (TxID). It starts with <code>0x</code> and is 66 characters long.',
    ),
    "wallet_ui_view_request": (
        'عرض الطلب',
        'View request',
    ),
    "wallet_ui_wallet_disabled_help": (
        '\nالدفع بالنجوم متاح عند تفعيل فاتورة المنتج. شحن المحفظة اليدوي متوقف.',
        "\nUse the product's Stars invoice when available. Manual wallet top-ups are disabled.",
    ),
    "wallet_ui_wallet_home": (
        '💳 <b>محفظتك</b>\nالرصيد المتاح: <b>{balance:.2f} USD</b>',
        '💳 <b>Your wallet</b>\nAvailable balance: <b>{balance:.2f} USD</b>',
    ),
    "wallet_ui_wallet_label": (
        'المحفظة',
        'Wallet',
    ),
}

def ui_text(lang, key, **values):
    return UI_MESSAGES[key][0 if lang == "ar" else 1].format(**values)
