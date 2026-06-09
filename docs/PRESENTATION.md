# Al Tahtheeb Equity Trading Module
## نظام تداول أسهم التحذيب
### Feature Presentation Document · وثيقة عرض المميزات

**Version / الإصدار:** 19.0.1.0.0  
**Author / المطوّر:** iCloud Solutions  
**Platform / المنصة:** Odoo 19 Enterprise  
**Date / التاريخ:** June 2026  

---

## Table of Contents · فهرس المحتويات

1. [Executive Summary / الملخص التنفيذي](#1-executive-summary)
2. [Module Architecture / معمارية النظام](#2-module-architecture)
3. [Feature 1 — Interactive Backend Dashboard / لوحة التحكم التفاعلية](#3-feature-1--backend-dashboard)
4. [Feature 2 — Internal Share Marketplace / سوق الأسهم الداخلي](#4-feature-2--internal-share-marketplace)
5. [Feature 3 — Legal E-Signature Workflow / سير عمل التوقيع القانوني الإلكتروني](#5-feature-3--legal-e-signature-workflow)
6. [Feature 4 — Saudi Statutory Compliance / الامتثال للأنظمة السعودية](#6-feature-4--saudi-statutory-compliance)
7. [Feature 5 — FIFO Portfolio & Trade Tracking / محفظة الوارد أولاً وتتبع التداول](#7-feature-5--fifo-portfolio--trade-tracking)
8. [Feature 6 — Investment Fund & Portfolio Revaluation / صناديق الاستثمار وإعادة التقييم](#8-feature-6--investment-fund--portfolio-revaluation)
9. [Feature 7 — Zakat Asset Valuation / تقييم أصول الزكاة](#9-feature-7--zakat-asset-valuation)
10. [Feature 8 — Tamper-Proof Audit Trail / سجل التدقيق غير القابل للتلاعب](#10-feature-8--tamper-proof-audit-trail)
11. [Feature 9 — Stakeholder Self-Service Portal / بوابة الخدمة الذاتية للمساهمين](#11-feature-9--stakeholder-self-service-portal)
12. [Feature 10 — Governance Configuration / إعدادات الحوكمة](#12-feature-10--governance-configuration)
13. [Security & Access Control / الأمان والتحكم في الوصول](#13-security--access-control)
14. [Automation & Scheduled Tasks / الأتمتة والمهام المجدولة](#14-automation--scheduled-tasks)
15. [Technical Stack / المكدس التقني](#15-technical-stack)
16. [Deployment Checklist / قائمة التحقق قبل التشغيل](#16-deployment-checklist)

---

## 1. Executive Summary
## الملخص التنفيذي

**EN** — *Al Tahtheeb Equity Trading* is an enterprise Odoo 19 module that automates the complete lifecycle of internal share trading for closed joint-stock companies operating under Saudi regulations. It extends Odoo's native **equity** module with a structured marketplace, a legally-binding e-signature workflow, FIFO portfolio accounting, Zakat-compliant asset classification, and a real-time OWL backend dashboard — all within a bilingual (Arabic/English) environment.

**AR** — وحدة *تداول أسهم التحذيب* هي وحدة أودو 19 المؤسسية التي تُؤتمت دورة حياة تداول الأسهم الداخلية الكاملة للشركات المساهمة المقفلة العاملة وفق الأنظمة السعودية. تُوسّع النظامَ الأصلي لأودو (**equity**) بسوق منظّم، وسير عمل توقيع إلكتروني مُلزم قانونياً، ومحاسبة محفظة بطريقة الوارد أولاً صادر أولاً، وتصنيف أصول متوافق مع الزكاة، ولوحة تحكم خلفية OWL في الوقت الفعلي — كل ذلك في بيئة ثنائية اللغة (عربي/إنجليزي).

---

### Key Benefits / المزايا الرئيسية

| # | English | العربية |
|---|---------|---------|
| ✅ | Full trade lifecycle management from listing to legal signature | إدارة دورة حياة التداول الكاملة من الإعلان إلى التوقيع القانوني |
| ✅ | Saudi CJSC statutory compliance built in | الامتثال النظامي لشركة المساهمة المقفلة السعودية مدمج |
| ✅ | Tamper-proof SHA-256 audit logs | سجلات تدقيق غير قابلة للتلاعب بخوارزمية SHA-256 |
| ✅ | Self-service portal for shareholders | بوابة خدمة ذاتية للمساهمين |
| ✅ | FIFO cost basis & realized gain/loss tracking | تتبع أساس التكلفة والأرباح/الخسائر المحققة بطريقة الوارد أولاً |
| ✅ | Zakat base & deductible classification per GAZT/ZATCA | تصنيف وعاء ومخصوم الزكاة وفق هيئة الزكاة والضريبة والجمارك |
| ✅ | Automated market price sync & period-end revaluation | مزامنة أسعار السوق وإعادة التقييم في نهاية الفترة بشكل آلي |
| ✅ | Real-time interactive backend dashboard (OWL) | لوحة تحكم خلفية تفاعلية في الوقت الفعلي (OWL) |

---

## 2. Module Architecture
## معمارية النظام

**EN** — The module is built as an extension layer on top of Odoo 19's native `equity` module. It adds 11 new models, extends 3 native models, and introduces a full portal + backend UI layer without touching core Odoo source code.

**AR** — الوحدة مبنية كطبقة امتداد فوق وحدة `equity` الأصلية في أودو 19. تضيف 11 نموذجاً جديداً، وتُوسّع 3 نماذج أصلية، وتُقدّم طبقة واجهة مستخدم كاملة للبوابة والواجهة الخلفية دون المساس بكود أودو الأصلي.

```
ics_altahtheeb_equity_trading
├── models/
│   ├── equity_marketplace_board.py    ← New: Marketplace listing model
│   ├── equity_transaction.py          ← Extends: equity.transaction
│   ├── equity_trade_order.py          ← New: Trade order log
│   ├── equity_trade_audit_log.py      ← New: Tamper-proof audit log
│   ├── equity_transaction_disposal.py ← New: FIFO disposal wizard
│   ├── equity_lot_allocation.py       ← New: FIFO lot allocation
│   ├── equity_investment_fund.py      ← New: Investment fund
│   ├── equity_portfolio_asset.py      ← New: Portfolio asset + market sync
│   ├── equity_portfolio_revaluation.py← New: Period-end revaluation
│   ├── equity_security_class.py       ← Extends: equity.security.class
│   ├── res_company.py                 ← Extends: res.company (governance)
│   ├── res_config_settings.py         ← Extends: res.config.settings
│   ├── sign_request.py                ← Extends: sign.request
│   └── tools.py                       ← Utility: bilingual error helpers
├── controllers/
│   ├── portal.py                      ← Portfolio dashboard + sign flow
│   └── marketplace.py                 ← Marketplace portal routes
├── static/src/
│   └── components/equity_trading_dashboard/
│       ├── equity_trading_dashboard.js   ← OWL component
│       └── equity_trading_dashboard.xml  ← OWL template
└── views/                             ← 12 XML view files
```

### Dependencies / الاعتماديات

| Module | Purpose / الغرض |
|--------|-----------------|
| `equity` | Native cap table, transactions, valuations, security classes |
| `sign` | Legal e-signature envelopes & signer roles |
| `account` | Journal entries for revaluation |
| `mail` | Chatter, activities, email templates |
| `portal` | Shareholder self-service web portal |
| `website` | Portal layout & routing |

---

## 3. Feature 1 — Backend Dashboard
## الميزة 1 — لوحة التحكم التفاعلية

**EN** — A real-time interactive backend dashboard built with Odoo's OWL (Owl Web Library) framework. Provides at-a-glance KPIs, Chart.js visualizations, and quick navigation for equity trading managers.

**AR** — لوحة تحكم خلفية تفاعلية في الوقت الفعلي مبنية بإطار عمل OWL من أودو. تُوفّر مؤشرات أداء رئيسية للوهلة الأولى، ومخططات بيانية باستخدام Chart.js، وتنقل سريع لمديري تداول الأسهم.

### KPI Cards / بطاقات المؤشرات

| Indicator | Description (EN) | الوصف (AR) |
|-----------|-----------------|------------|
| Active Listings | Count of currently published marketplace listings | عدد الإعلانات المنشورة حالياً في السوق |
| Pending Signatures | Transactions awaiting legal e-signature | المعاملات بانتظار التوقيع الإلكتروني القانوني |
| Portfolio Value | Total current market value of all portfolio assets | إجمالي القيمة السوقية الحالية لجميع أصول المحفظة |
| Completed This Month | Transactions reaching "Done" state in current month | المعاملات التي وصلت لحالة "منجزة" في الشهر الحالي |

### Charts / المخططات البيانية

| Chart | Type | Description (EN) | الوصف (AR) |
|-------|------|-----------------|------------|
| Listing Status Breakdown | Doughnut | Color-coded distribution of all listings by state | توزيع الإعلانات بالألوان حسب الحالة |
| Weekly New Listings | Bar | Last 8 weeks of listing creation activity | نشاط إنشاء الإعلانات خلال آخر 8 أسابيع |

### Interactive Elements / العناصر التفاعلية

- **EN:** All KPI cards, table rows, and action items are clickable — navigate directly to the relevant backend view.
- **AR:** جميع بطاقات المؤشرات وصفوف الجدول وعناصر الإجراءات قابلة للنقر — للانتقال مباشرة إلى العرض الخلفي ذي الصلة.

- **EN:** "Refresh" button re-fetches all data server-side without page reload.
- **AR:** زر "تحديث" يُعيد جلب جميع البيانات من الخادم دون إعادة تحميل الصفحة.

---

## 4. Feature 2 — Internal Share Marketplace
## الميزة 2 — سوق الأسهم الداخلي

**EN** — A structured marketplace board where shareholders can post sell offers or buying requests for internal equity transfers. Includes a full state machine lifecycle with governance approval and ROFR enforcement.

**AR** — لوحة سوق منظّمة حيث يمكن للمساهمين نشر عروض البيع أو طلبات الشراء لنقل الأسهم الداخلي. تتضمن دورة حياة كاملة بآلة حالة مع اعتماد الحوكمة وتطبيق حق الأولوية في الشراء.

### Listing Lifecycle / دورة حياة الإعلان

```
Draft → Published → Matched → Approved → Signature In Progress → Done
                                                               ↑
                              ← ← ← (stale signature release) ←
                        Any state → Cancelled
```

| State (EN) | الحالة (AR) | Description |
|------------|-------------|-------------|
| Draft | مسودة | Created, pending internal review |
| Published | منشور | Visible on portal with active ROFR window (15 days) |
| Matched | مطابق | Counterparty identified, awaiting governance approval |
| Approved | معتمد | Governance approved, transaction being created |
| Signature In Progress | التوقيع قيد التنفيذ | Legal e-signature envelope active |
| Done | منجز | Transfer complete, cap table updated |
| Cancelled | ملغى | Administratively voided |

### Key Controls / الضوابط الرئيسية

- **Right of First Refusal (ROFR):** 15-day window enforced on all sell offers before external buyers can match.  
  **حق الأولوية في الشراء:** فترة 15 يوماً مطبّقة على جميع عروض البيع قبل السماح للمشترين الخارجيين بالمطابقة.

- **Counterparty protection:** Portal users cannot spoof a different counterparty ID — the system always uses the authenticated user's partner.  
  **حماية الطرف المقابل:** لا يمكن لمستخدمي البوابة انتحال معرّف طرف مقابل مختلف — يستخدم النظام دائماً شريك المستخدم المُصادق عليه.

- **Self-matching prevention:** A shareholder cannot match their own listing.  
  **منع المطابقة الذاتية:** لا يمكن للمساهم مطابقة إعلانه الخاص.

---

## 5. Feature 3 — Legal E-Signature Workflow
## الميزة 3 — سير عمل التوقيع الإلكتروني القانوني

**EN** — Deep integration with Odoo Sign to produce legally-binding digital transfer agreements. The workflow is enforced server-side with two-party signing (shareholder → corporate authority) and automatic stale-signature handling.

**AR** — تكامل عميق مع أودو Sign لإنتاج اتفاقيات نقل رقمية مُلزمة قانونياً. يُطبَّق سير العمل من جانب الخادم بتوقيع طرفين (المساهم ← المفوض بالتوقيع) ومعالجة تلقائية للتوقيعات المنتهية.

### Workflow Steps / خطوات سير العمل

1. **EN:** Manager clicks "Initiate Legal Signature" on a confirmed equity transaction.  
   **AR:** ينقر المدير على "بدء التوقيع القانوني" في معاملة أسهم مؤكدة.

2. **EN:** System resolves Sign template, validates both signer partners have email addresses.  
   **AR:** يُحدّد النظام قالب التوقيع ويتحقق من امتلاك كلا الموقّعين عنواناً بريدياً.

3. **EN:** Sign envelope created → shareholder signs first, then corporate authority.  
   **AR:** يُنشأ مظروف التوقيع → يوقّع المساهم أولاً ثم المفوض بالتوقيع.

4. **EN:** Shareholder accesses the sign page via the self-service portal with embedded iframe.  
   **AR:** يصل المساهم إلى صفحة التوقيع عبر البوابة الذاتية بإطار مضمّن.

5. **EN:** On completion, the transaction auto-advances to "Legally Signed" → "Done".  
   **AR:** عند الاكتمال، تنتقل المعاملة تلقائياً إلى "موقّع قانونياً" ← "منجزة".

### Stale Signature Protection / حماية التوقيع المنتهي

- **EN:** A nightly cron job automatically cancels Sign envelopes that remain unsigned beyond the configured timeout (default: 5 days). The linked marketplace listing is released back to "Published" so new bids can be accepted.  
- **AR:** مهمة دورية ليلية تُلغي تلقائياً مظاريف التوقيع التي تبقى غير موقّعة بعد انتهاء المهلة المُضبوطة (افتراضي: 5 أيام). يُعاد إعلان السوق المرتبط إلى حالة "منشور" لقبول عروض جديدة.

---

## 6. Feature 4 — Saudi Statutory Compliance
## الميزة 4 — الامتثال للأنظمة السعودية

**EN** — Built-in enforcement of Saudi closed joint-stock company (CJSC / شركة مساهمة مقفلة) regulations, configurable per company.

**AR** — تطبيق مدمج لأنظمة شركة المساهمة المقفلة السعودية، قابل للضبط لكل شركة.

### Statutory Checks / الفحوصات النظامية

| Check (EN) | الفحص (AR) | Regulation |
|------------|-----------|------------|
| Minimum shareholders count | الحد الأدنى لعدد المساهمين | CJSC bylaws (≥ 2, configurable) |
| Maximum ownership concentration | الحد الأقصى لتركز الملكية | Internal governance (0–100%) |
| Maximum voting power concentration | الحد الأقصى لتركز قوة التصويت | Internal governance (0–100%) |
| Maximum votes per share | الحد الأقصى للأصوات لكل سهم | Company bylaws |
| ROFR window enforcement | تطبيق فترة حق الأولوية | Internal transfer rules |
| ROFR waiver by board | التنازل عن حق الأولوية من مجلس الإدارة | Board resolution |

### Configuration / الضبط

All governance thresholds are set in **Settings → Equity Trading Governance**, stored on `res.company`, and enforced at transaction time on every `check_saudi_statutory_bounds()` call.

جميع عتبات الحوكمة تُضبط في **الإعدادات ← حوكمة تداول الأسهم**، مُخزّنة على `res.company`، ومُطبَّقة عند وقت المعاملة عند كل استدعاء لـ `check_saudi_statutory_bounds()`.

---

## 7. Feature 5 — FIFO Portfolio & Trade Tracking
## الميزة 5 — محفظة الوارد أولاً وتتبع التداول

**EN** — Complete FIFO (First In, First Out) cost accounting for share disposals, producing accurate realized gain/loss calculations and a traceable lot allocation chain.

**AR** — محاسبة تكلفة كاملة بطريقة الوارد أولاً صادر أولاً للتخلص من الأسهم، مما يُنتج حسابات دقيقة للأرباح/الخسائر المحققة وسلسلة تخصيص دفعات قابلة للتتبع.

### FIFO Disposal Wizard / معالج التخلص بطريقة الوارد أولاً

1. **EN:** Triggered from a "Done" sell transaction.  
   **AR:** يُطلَق من معاملة بيع "منجزة".

2. **EN:** Matches sell quantity against remaining buy lots (oldest first) for the same holder and share class.  
   **AR:** يُطابق كمية البيع مع الدفعات الشرائية المتبقية (الأقدم أولاً) لنفس الحامل وفئة السهم.

3. **EN:** Calculates per-lot: cost basis, proceeds, and realized gain/loss.  
   **AR:** يحسب لكل دفعة: أساس التكلفة، والعائدات، والربح/الخسارة المحققة.

4. **EN:** Creates `equity.lot.allocation` records that permanently link buy and sell transactions.  
   **AR:** يُنشئ سجلات `equity.lot.allocation` تُربط دائماً بين معاملات الشراء والبيع.

### Trade Order Log / سجل أمر التداول

- **EN:** Every confirmed equity transaction automatically generates a `equity.trade.order` record with trade side (buy/sell/other), quantity, unit price, and amount total. Once confirmed or posted, the record is **immutable**.  
- **AR:** كل معاملة أسهم مؤكدة تُنشئ تلقائياً سجل `equity.trade.order` مع جانب التداول (شراء/بيع/أخرى) والكمية وسعر الوحدة والمبلغ الإجمالي. بعد التأكيد أو الترحيل، يكون السجل **غير قابل للتعديل**.

---

## 8. Feature 6 — Investment Fund & Portfolio Revaluation
## الميزة 6 — صناديق الاستثمار وإعادة التقييم

**EN** — Multi-fund investment tracking with automated period-end fair-value revaluation posting to the general ledger.

**AR** — تتبع استثمارات متعددة الصناديق مع ترحيل آلي لإعادة التقييم بالقيمة العادلة في نهاية الفترة إلى دفتر الأستاذ العام.

### Investment Fund Configuration / ضبط صندوق الاستثمار

| Field (EN) | الحقل (AR) | Purpose |
|------------|-----------|---------|
| Fund Code | رمز الصندوق | Unique identifier per company |
| Revaluation Journal | دفتر يومية إعادة التقييم | Journal for FV adjustment entries |
| Book Value Account | حساب القيمة الدفترية | Historical cost GL account |
| Asset Revaluation Account | حساب إعادة تقييم الأصول | Balance-sheet FV adjustment account |
| Unrealized Gain/Loss Account | حساب الأرباح/الخسائر غير المحققة | P&L or OCI account |
| Financial Reporting Tags | وسوم التقارير المالية | Statutory reporting tags on GL accounts |
| Cap Table Holders Filter | فلتر حاملي جدول رأس المال | Restrict which partners are counted |

### Portfolio Asset Market Sync / مزامنة أسعار السوق لأصول المحفظة

- **EN:** A cron job runs every 15 minutes to apply staged market feed prices to portfolio assets. Uses row-level locking with exponential retry (up to 8 retries, configurable) to handle concurrent updates safely.  
- **AR:** مهمة دورية كل 15 دقيقة لتطبيق أسعار تغذية السوق المُخزّنة مؤقتاً على أصول المحفظة. تستخدم قفل مستوى الصف مع إعادة المحاولة الأسية (حتى 8 مرات، قابل للضبط) للتعامل الآمن مع التحديثات المتزامنة.

### Period-End Revaluation / إعادة التقييم في نهاية الفترة

- **EN:** Monthly cron generates draft revaluation journal entries for each fund, comparing current market value against book value to produce unrealized gain/loss postings.  
- **AR:** مهمة شهرية تُنشئ قيود يومية إعادة تقييم مسودة لكل صندوق، مُقارِنةً القيمة السوقية الحالية بالقيمة الدفترية لإنتاج قيود الأرباح/الخسائر غير المحققة.

---

## 9. Feature 7 — Zakat Asset Valuation
## الميزة 7 — تقييم أصول الزكاة

**EN** — Zakat-compliant classification and valuation of equity portfolio holdings per GAZT/ZATCA rules for Saudi listed and unlisted shares.

**AR** — تصنيف وتقييم أصول محفظة الأسهم المتوافق مع الزكاة وفق قواعد هيئة الزكاة والضريبة والجمارك للأسهم السعودية المدرجة وغير المدرجة.

### Zakat Classification / تصنيف الزكاة

| Classification (EN) | التصنيف (AR) | Zakat Treatment |
|--------------------|--------------|-----------------|
| Trading (Short-term) | تداول (قصير الأجل) | Included in Zakat base at year-end FMV (qty × market price) |
| Strategic / Long-term | استراتيجي / طويل الأجل | Excluded from Zakat base; deductible if investee meets conditions |

### Dedicated Report / التقرير المخصص

The **Zakat Asset Valuation Report** lists all portfolio assets with:

تقرير **تقييم أصول الزكاة** يسرد جميع أصول المحفظة مع:

- Total Book Value / إجمالي القيمة الدفترية
- Total Market Value / إجمالي القيمة السوقية
- Zakat Base Contribution Value / قيمة مساهمة وعاء الزكاة
- Zakat Deductible Value / قيمة مخصوم الزكاة
- Classification breakdown / تفصيل التصنيف

---

## 10. Feature 8 — Tamper-Proof Audit Trail
## الميزة 8 — سجل التدقيق غير القابل للتلاعب

**EN** — Every amendment to a trade order generates a cryptographically signed, immutable audit log entry, providing a legally defensible chain of custody for all equity trade records.

**AR** — كل تعديل على أمر تداول يُنشئ إدخال سجل تدقيق غير قابل للتغيير وموقَّع تشفيرياً، مما يُوفّر سلسلة حضانة قابلة للدفاع قانونياً لجميع سجلات تداول الأسهم.

### Audit Log Fields / حقول سجل التدقيق

| Field (EN) | الحقل (AR) | Description |
|------------|-----------|-------------|
| Name | الاسم | Unique sequence reference (TRD-AUD-YYYY-XXXX) |
| Trade Order | أمر التداول | Linked trade order |
| User | المستخدم | Who made the change |
| Action | الإجراء | Amendment or Administrative Override |
| Amendment Reason | سبب التعديل | Free-text justification |
| Snapshot Before Change | لقطة قبل التغيير | JSON state of the record before amendment |
| Changed Fields | الحقول المعدّلة | Map of field → new value |
| Integrity Signature | توقيع النزاهة | SHA-256 digest of the snapshot payload |

### Immutability Enforcement / تطبيق عدم قابلية التغيير

- **EN:** `write()` and `unlink()` on `equity.trade.audit.log` are **blocked at the ORM level** — no user, including system administrators, can modify or delete a log entry after creation.  
- **AR:** `write()` و`unlink()` على `equity.trade.audit.log` **محظوران على مستوى ORM** — لا يمكن لأي مستخدم، بما في ذلك مديرو النظام، تعديل أو حذف إدخال سجل بعد إنشائه.

---

## 11. Feature 9 — Stakeholder Self-Service Portal
## الميزة 9 — بوابة الخدمة الذاتية للمساهمين

**EN** — A dedicated web portal allows shareholders to manage their equity affairs independently, reducing administrative overhead and improving transparency.

**AR** — بوابة ويب مخصصة تتيح للمساهمين إدارة شؤونهم المتعلقة بالأسهم باستقلالية، مما يُقلّل العبء الإداري ويُحسّن الشفافية.

### Portal Pages / صفحات البوابة

#### My Portfolio Dashboard / لوحة محفظتي

- **EN:** Displays live share allocations, total shares owned, estimated portfolio valuation, pending signature alerts, and complete transaction history.  
- **AR:** تعرض توزيعات الأسهم المباشرة وإجمالي الأسهم المملوكة والتقييم التقديري للمحفظة وتنبيهات التوقيع المعلّق وكامل سجل المعاملات.

#### Marketplace Dashboard / لوحة السوق

- **EN:** Lists all active sell offers with ROFR badge timers, share class details, quantity, and price. Shareholders can submit buy proposals via a modal confirmation dialog.  
- **AR:** تسرد جميع عروض البيع النشطة مع مؤشرات فترة حق الأولوية وتفاصيل فئة السهم والكمية والسعر. يمكن للمساهمين تقديم عروض الشراء عبر نافذة تأكيد.

#### Electronic Signature Page / صفحة التوقيع الإلكتروني

- **EN:** Secure embedded Odoo Sign iframe with transaction context (transaction ID, date, share class). The shareholder's cryptographic signature is processed directly by Odoo Sign.  
- **AR:** إطار أودو Sign المضمّن الآمن مع سياق المعاملة (رقم المعاملة والتاريخ وفئة السهم). يُعالَج التوقيع التشفيري للمساهم مباشرة بواسطة أودو Sign.

### Portal Home Integration / تكامل الصفحة الرئيسية للبوابة

Two cards are injected into `/my/home`:

يُضاف بطاقتان إلى `/my/home`:

- **My Portfolio** — links to portfolio dashboard with live holdings count  
  **محفظتي** — رابط للوحة المحفظة مع عدد الممتلكات المباشرة

- **Share Marketplace** — links to marketplace with active listing count  
  **سوق الأسهم** — رابط للسوق مع عدد الإعلانات النشطة

---

## 12. Feature 10 — Governance Configuration
## الميزة 10 — إعدادات الحوكمة

**EN** — All statutory thresholds are centrally configurable per company via the standard Odoo Settings UI, using a dedicated "Equity Trading Governance" block visible only to managers.

**AR** — جميع العتبات النظامية قابلة للضبط مركزياً لكل شركة عبر واجهة إعدادات أودو القياسية، باستخدام كتلة "حوكمة تداول الأسهم" المرئية للمديرين فقط.

| Setting (EN) | الإعداد (AR) | Default |
|-------------|--------------|---------|
| Saudi CJSC Minimum Shareholders | الحد الأدنى للمساهمين في شركة مساهمة مقفلة | 2 |
| ROFR Window (Days) | فترة حق الأولوية (بالأيام) | 15 |
| Max Ownership Concentration (%) | الحد الأقصى لتركز الملكية (%) | — |
| Max Voting Power Concentration (%) | الحد الأقصى لتركز قوة التصويت (%) | — |
| Max Votes per Share (Bylaws) | الحد الأقصى للأصوات لكل سهم | — |

---

## 13. Security & Access Control
## الأمان والتحكم في الوصول

### User Roles / أدوار المستخدمين

| Role (EN) | الدور (AR) | Inherited From | Capabilities |
|-----------|-----------|---------------|-------------|
| Equity Trading User | مستخدم تداول الأسهم | `equity.group_equity_viewer` + `base.group_user` | Read all equity data; create/edit marketplace listings and portfolio assets |
| Equity Trading Manager | مدير تداول الأسهم | Trading User + `equity.group_equity_manager` | Full CRUD on all module models; access to audit logs and governance settings |

### Record Rules / قواعد السجلات

- **EN:** Portal users can only read marketplace listings in "published" state. They cannot read listings in draft, matched, approved, or cancelled states.  
- **AR:** يمكن لمستخدمي البوابة قراءة إعلانات السوق في حالة "منشور" فقط. لا يمكنهم قراءة الإعلانات في حالات المسودة أو المطابقة أو المعتمدة أو الملغاة.

- **EN:** Internal users can read all listings within their company.  
- **AR:** يمكن للمستخدمين الداخليين قراءة جميع الإعلانات ضمن شركتهم.

---

## 14. Automation & Scheduled Tasks
## الأتمتة والمهام المجدولة

| Task (EN) | المهمة (AR) | Schedule | Model |
|-----------|-----------|---------|-------|
| Expire Stale Signature Requests | إنهاء طلبات التوقيع المنتهية | Daily at 02:00 UTC | `equity.transaction` |
| Sync Market Feed Prices | مزامنة أسعار تغذية السوق | Every 15 minutes | `equity.portfolio.asset` |
| Period-End Portfolio Revaluation | إعادة تقييم المحفظة في نهاية الفترة | Monthly at 03:00 UTC | `equity.portfolio.revaluation` |

### System Parameters / معاملات النظام

| Key | Default | Description |
|-----|---------|-------------|
| `stale_signature_days` | 5 | Days before unsigned envelope is auto-cancelled |
| `market_feed_batch_size` | 50 | Portfolio assets processed per cron batch |
| `market_feed_max_retries` | 8 | Maximum retry attempts for failed feed sync |
| `market_feed_retry_base_minutes` | 5 | Base minutes for exponential retry backoff |

---

## 15. Technical Stack
## المكدس التقني

| Layer | Technology |
|-------|-----------|
| Backend Framework | Odoo 19 Enterprise (Python 3, ORM) |
| Frontend Framework | OWL (Odoo Web Library) — reactive components |
| Charts | Chart.js (loaded from Odoo web assets) |
| Portal UI | QWeb templates + Bootstrap 5 |
| E-Signature | Odoo Sign (PDF envelope, role-based) |
| Database | PostgreSQL (with SKIP LOCKED for concurrency) |
| Security | SHA-256 audit log signatures; row-level record rules |
| i18n | Full Arabic (ar_001.po) — 400+ translated strings |

---

## 16. Deployment Checklist
## قائمة التحقق قبل التشغيل

### Prerequisites / المتطلبات المسبقة

- [ ] **EN:** Odoo 19 Enterprise with `equity` and `sign` modules installed.  
  **AR:** أودو 19 المؤسسي مع تثبيت وحدتي `equity` و`sign`.

- [ ] **EN:** At least one equity security class of type "shares" configured in Odoo's native equity module.  
  **AR:** فئة أسهم واحدة على الأقل من نوع "أسهم" مُضبوطة في وحدة equity الأصلية.

- [ ] **EN:** An Odoo Sign template with exactly two signer roles (Shareholder + Corporate Authority Representative) must be created before initiating the legal flow.  
  **AR:** يجب إنشاء قالب أودو Sign بدورَي موقّعين بالضبط (المساهم + المفوض بالتوقيع) قبل بدء الإجراء القانوني.

### Post-Installation Configuration / الضبط بعد التثبيت

- [ ] **EN:** Navigate to **Settings → Equity Trading Governance** and configure CJSC thresholds.  
  **AR:** الانتقال إلى **الإعدادات ← حوكمة تداول الأسهم** وضبط عتبات شركة المساهمة المقفلة.

- [ ] **EN:** Create at least one Investment Fund with GL accounts for portfolio revaluation.  
  **AR:** إنشاء صندوق استثمار واحد على الأقل مع حسابات دفتر الأستاذ لإعادة تقييم المحفظة.

- [ ] **EN:** Assign **Equity Trading User** or **Manager** role to relevant internal users.  
  **AR:** تعيين دور **مستخدم تداول الأسهم** أو **المدير** للمستخدمين الداخليين المعنيين.

- [ ] **EN:** Grant portal access to shareholder contacts and enable the `equity_access_token` on their partner records (via native equity module).  
  **AR:** منح وصول البوابة لجهات اتصال المساهمين وتفعيل `equity_access_token` على سجلات شركائهم (عبر وحدة equity الأصلية).

- [ ] **EN:** Verify the three scheduled cron jobs are active in **Settings → Technical → Scheduled Actions**.  
  **AR:** التحقق من أن المهام الثلاث المجدولة نشطة في **الإعدادات ← تقني ← الإجراءات المجدولة**.

---

## Appendix — Arabic Glossary
## ملحق — المصطلحات العربية

| English Term | المصطلح العربي |
|-------------|----------------|
| Equity Trading | تداول الأسهم |
| Marketplace Listing | إعلان السوق |
| Right of First Refusal (ROFR) | حق الأولوية في الشراء |
| E-Signature | التوقيع الإلكتروني |
| Cap Table | جدول رأس المال |
| Portfolio Asset | أصل المحفظة |
| Investment Fund | صندوق الاستثمار |
| FIFO Disposal | التخلص بطريقة الوارد أولاً صادر أولاً |
| Lot Allocation | تخصيص الدفعات |
| Realized Gain/Loss | الربح/الخسارة المحققة |
| Period-End Revaluation | إعادة التقييم في نهاية الفترة |
| Fair Value | القيمة العادلة |
| Book Value | القيمة الدفترية |
| Trade Order | أمر التداول |
| Audit Log | سجل التدقيق |
| Stale Signature | التوقيع المنتهي |
| Governance | الحوكمة |
| Shareholder | المساهم |
| Corporate Authority | المفوض بالتوقيع |
| Security Class | فئة الأسهم |
| Counterparty | الطرف المقابل |
| Statutory Compliance | الامتثال النظامي |
| Zakat Base | وعاء الزكاة |
| Zakat Deductible | مخصوم الزكاة |
| CJSC (Closed Joint-Stock Company) | شركة مساهمة مقفلة |
| GAZT/ZATCA | هيئة الزكاة والضريبة والجمارك |

---

*Document prepared by iCloud Solutions — Al Tahtheeb Equity Trading v19.0.1.0.0*  
*أُعدّت هذه الوثيقة بواسطة iCloud Solutions — نظام تداول أسهم التحذيب الإصدار 19.0.1.0.0*
