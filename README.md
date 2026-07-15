# 🤖 Edu Group Robot (Lang Bot v5.0)

Telegram **superguruh**larida ishlaydigan ta'lim/bilim tekshirish o'yin-boti. Foydalanuvchilar mavzular (topic) bo'yicha savol-javob o'yinlarida ishtirok etadi, adminlar esa forum-topic asosida savollar bazasini boshqaradi. Tarif tizimi, referral, Telegram Stars orqali to'lov va Telegram'ning o'zini backup sifatida ishlatuvchi noodatiy arxitekturaga ega.

---

## ✨ Asosiy xususiyatlar

- **4 ta o'yin rejimi:** standart, tezkor (⏱ vaqt limitli), tarjima (lang), va admin-mode (qo'lda g'olib belgilash)
- **4 bosqichli tarif tizimi:** Free / PLUS ✨ / Premium 💎 / VIP 👑 — har birida topic va savol limiti farqlanadi
- **Referral tizimi:** Free foydalanuvchi do'stlarini taklif qilib qo'shimcha topic ochishi mumkin
- **Telegram Stars orqali to'lov** — tarif sotib olish to'g'ridan-to'g'ri Telegram ichida
- **Forum-topic boshqaruvi** — har tarif va sinf uchun alohida topic avtomatik yaratiladi
- **So'kinish/taqiqlangan so'z filtri** — oddiy va "og'ir" darajali so'zlar, ogohlantirish tizimi
- **To'liq admin panel** — inline tugmalar orqali boshqariladigan, kod yozishni talab qilmaydigan boshqaruv
- **Avtomatik zaxira nusxa (backup)** — ma'lumotlar hech qachon yo'qolmaydigan tarzda loyihalangan (pastda batafsil)

---

## 🏗 Arxitektura — nega bunday qurilgan

Bot **Render'ning bepul tarifida** ishlashga moslab qurilgan. Bepul tarifning asosiy muammosi: **disk vaqtinchalik** — bot qayta ishga tushganda (deploy, sleep/wake, crash) diskdagi barcha fayllar o'chib ketadi. Shu sababli loyiha oddiy ma'lumotlar bazasi (Postgres, SQLite fayli) ishlatmaydi — buning o'rniga **Telegram'ning o'zini doimiy xotira sifatida** ishlatadi:

```
┌─────────────────┐     doimiy pin xabar      ┌──────────────────┐
│ CONTROL_GROUP    │ ───────────────────────▶ │  "Qaysi DB guruh  │
│ (kichik, oddiy    │                          │   ishlatilyapti + │
│  guruh)           │                          │   oxirgi backup   │
└─────────────────┘                          │   qayerda"        │
                                               └──────────────────┘
                                                        │
                                                        ▼
┌─────────────────┐    JSON export (pin)      ┌──────────────────┐
│  DB / Backup     │ ◀─────────────────────── │  bot.py / core.py │
│  SUPERGROUP      │ ────────────────────────▶│  (JSON o'qish/    │
│  (forum-topic'li) │    auto-restore (start)  │   yozish)         │
└─────────────────┘                          └──────────────────┘
```

**Nega bu aqlli yechim:** Agar faqat `SUPERGROUP_ID` ENV-o'zgaruvchida saqlansa, uni almashtirish uchun har safar qayta deploy kerak bo'lardi. `CONTROL_GROUP_ID` esa **hech qachon o'zgarmaydigan** kichik guruh — u yerdagi bitta pin xabar ichida qaysi guruh "hozirgi DB" ekanligi yozilgan. Shu tufayli:

- `/setgroup` komandasi bilan DB guruhni **qayta deploy qilmasdan** almashtirish mumkin
- Render diskini tozalab tashlasa ham, bu ma'lumot yo'qolmaydi — chunki u kod yoki faylda emas, **Telegram serverida** turibdi
- Bot ishga tushganda avtomatik ravishda oxirgi backup'ni topib, o'zini tiklaydi (`auto_restore_on_startup`)

Oddiy ma'lumotlar (`users.json`, `admins.json`, `chats.json` va h.k.) esa har o'zgarishdan **3 soniya keyin** (debounce — Telegram flood-limitiga tegib ketmaslik uchun) avtomatik eksport qilinib, DB guruhdagi maxsus topic'ga pin qilinadi.

---

## 📁 Loyiha tuzilishi

| Fayl | Vazifasi |
|---|---|
| `core.py` | Poydevor: konfiguratsiya, JSON saqlash, backup/restore, o'yin logikasi, klaviaturalar, Stars to'lov |
| `bot.py` | Handlerlar: admin komandalar, matn/media handler, callback handler, `main()` |
| `tests/` | 7 ta test fayli — export/restore, bulk-add, broadcast va boshqa muhim oqimlar uchun |
| `requirements.txt` | Ishlab chiqarish uchun kutubxonalar |
| `requirements-dev.txt` | + `pytest`, `pytest-asyncio` (test uchun) |
| `.gitignore` | `*.json` va `topics/` repo'ga tushmaydi — ma'lumotlar faqat Telegram'da saqlanadi |

---

## ⚙️ O'rnatish

### Kerakli ENV o'zgaruvchilar

| Nomi | Majburiymi | Tavsifi |
|---|---|---|
| `BOT_TOKEN` | ✅ | BotFather'dan olingan token |
| `SUPERADMIN_ID` | ✅ | Bosh administratorning Telegram ID'si |
| `WEBHOOK_URL` yoki `RENDER_EXTERNAL_URL` | ✅ | Webhook uchun tashqi manzil |
| `WEBHOOK_SECRET` | ⬜ | Webhook'ni himoyalash uchun maxfiy so'z |
| `CONTROL_GROUP_ID` | ⬜ (lekin tavsiya etiladi) | Yuqorida tushuntirilgan doimiy nazorat guruhi |
| `SUPERGROUP_ID` | ⬜ | Orqaga moslik uchun — boshlang'ich DB guruh (CONTROL_GROUP bo'lmasa ishlatiladi) |
| `PORT` | ⬜ | Standart: `8080` |

### O'rnatish qadamlari

```bash
git clone https://github.com/muslihiddinlive/Edu-Group-Robot.git
cd Edu-Group-Robot
pip install -r requirements.txt

# Test uchun (ixtiyoriy):
pip install -r requirements-dev.txt
pytest tests/
```

ENV o'zgaruvchilarni sozlab, `python bot.py` bilan ishga tushiring (Render'da avtomatik webhook rejimida ishlaydi).

---

## 🎮 O'yin rejimlari

| Buyruq | Rejim |
|---|---|
| `/newgame <topic>` | Standart — savolga to'g'ri javob yozish |
| `/newgame <topic> langmode` | Tarjima o'yini — savol/javob tomoni tasodifiy almashadi |
| `/newgame <topic> speed [vaqt]` | Tezkor — masalan `/newgame ingliz speed 30s`, vaqt tugasa savol o'tkaziladi |
| Admin-mode | Admin savolni o'qiydi, g'olibni qo'lda ✅ bosib belgilaydi |

---

## 💳 Tarif tizimi

| Tarif | Topic limiti | Savol/topic | Narxi (Stars) |
|---|---|---|---|
| Free | 1 (+referral orqali 5 tagacha) | 10 | — |
| PLUS ✨ | 10 | 100 | 25 |
| Premium 💎 | 20 | 200 | 50 |
| VIP 👑 | 70 | 1500 + admin qo'sha oladi (max 2) | 500 |

*Free foydalanuvchi har 3 ta referaldan +1 topic oladi.*

---

## 📜 Buyruqlar

### Ommaviy (hamma uchun)
`/start` · `/contact` · `/cancel` · `/newgame` · `/endgame` · `/scores` · `/skip`

### Admin uchun (yuqoridagilarga qo'shimcha)
**Topic/savol:** `/newtopic` `/listtopics` `/deletetopic` `/setprize` `/edittopicaccess` `/addq` `/bulkq` `/createtopic`

**Adminlar:** `/addadmin` `/removeadmin` `/listadmins` `/editadmin` `/addchatadmin`

**Moderatsiya:** `/addbadword` `/addsevereword` `/addsacredname` `/addwarning` `/listbadwords` `/removebadword` `/removewarning` `/requireadmin`

**Boshqaruv:** `/broadcast` `/export` `/restore` `/setgroup` `/setprice` `/setchannel` `/togglereaction` `/sendas` `/setdisplayname` `/getemojiid` `/userinfo` `/listusers` `/delmsgs` `/delbotmsg` `/del` `/done` `/listgames`

---

## 🧪 Testlar

```bash
pytest tests/ -v
```

Testlar real topilgan xatolarga asoslangan — masalan `export/restore roundtrip`, `bulk-add`dagi jim (silent) xatoliklar, `deletetopic` va `broadcast` oqimlari.

---

## 🚀 Deploy (Render)

1. Render'da yangi **Web Service** yarating, repo'ni ulang
2. Yuqoridagi ENV o'zgaruvchilarni kiriting
3. Build: `pip install -r requirements.txt`, Start: `python bot.py`
4. Bot birinchi marta ishga tushganda `/setgroup` bilan DB guruhni belgilang (agar `CONTROL_GROUP_ID` ishlatilsa)

> 💡 Bepul tarifda bot uxlab qolishi mumkin (inactivity sleep) — webhook rejimi va Telegram-asosli backup aynan shu holatga chidamli bo'lish uchun tanlangan.
