# Alignment against the project description

> *"Develop a multimodal machine learning system to assist students and staff in
> recovering lost belongings on campus. Users should be able to report lost or
> found objects using descriptions and/or photographs. The system should use
> computer vision and natural language processing to identify similarities
> between lost and found reports and rank the most likely matches using visual,
> textual, temporal and location information."*

Status per clause, with the code that backs the claim. **Aligned** = true as
written. **Partial** = true but narrower than the sentence implies. **Gap** = not
true yet. Anything partial or gap is stated as future work, not implied.

| Clause | Status | Backed by | What is still narrower than the sentence |
|---|---|---|---|
| "multimodal machine learning system" | Partial | 14 evidence features across five families (visual, textual, attribute, temporal, location), learned pairwise re-ranker (`rerank.py`), fitted calibration (`calibration.py`) | Each modality is a classical encoder, not a deep one; no neural fusion network |
| "assist students and staff" | **Aligned** | Roles `student/staff/campus_admin/superadmin`, staff-gated confirm/reject and handover, per-campus scoping; student self-service: "My reports" in the browser's ownership tokens, prove-ownership, claim tracking, and a staff Desk queue | A student account is still not required — the browser token is the identity, which is deliberate for walk-up reporting but means clearing site data loses the shortcut |
| "in recovering lost belongings" | **Aligned** | Match identification + staff-ratified confirmation + ownership challenge (`recovery.py`: serial/marks/brand/colour questions, at least two required, prompts never contain their answers) + one-time hashed handover code + staff counter close (`claims/{id}/collect` → both reports `recovered`, audit entry) + outbox notifications at verify/confirm/collect | No QR scan, no photo-ID capture, no SMS/WhatsApp channel |
| "report lost or found objects" | **Aligned** | `kind=lost\|found`, anonymous allowed with contact + one-time ownership token, rate limits, `pending_review` moderation | — |
| "using descriptions and/or photographs" | **Aligned** | Text-only, text+photo, and **photo-only** (description optional on `/api/reports/upload`); up to **4 photos** per report, max-pooled into one image vector and returned as `image_urls` | — |
| "computer vision and natural language processing" | **Aligned, classical** | CV: HSV histogram + 4×4 colour grid + edge density + CIE76 colour difference, plus optional OCR (`ocr.py`, PII-redacted serials feeding `visible_text`). NLP: rule-based attribute extraction, hashed n-gram cosine, token Jaccard, serial/model assertions | No learned encoder, by decision: the DINOv2/bge-m3 adapters and the `vision` extra were removed, so the project loads and trains no neural network. OCR needs the native `tesseract` binary, so a fresh checkout reads no printed text |
| "identify similarities between lost and found reports" | **Aligned** | Cross-report pair scoring; 189 ground-truth pairs | — |
| "rank the most likely matches" | **Aligned** | Ordered shortlist, calibrated probability, margin, reason chips, deep-linkable cases | — |
| "visual … information" | **Aligned** | Image cosine + colour difference (`features.py`) | Classical descriptor quality |
| "textual … information" | **Aligned** | Dense cosine, token overlap, serial/model/brand/material/marks | No multilingual encoder |
| "temporal … information" | **Aligned** | Lost **interval** vs found timestamp, impossible-order suppression, per-category hold times | — |
| "location … information" | **Aligned** | Campus walk-graph distance over verified nodes (same-building boost) **and** haversine distance when both reports are pinned (`geo.py`, evidence weight 0.04, explained as a "… m apart (GPS)" chip) | — |

## Changes that would close each remaining gap

Ordered by value per hour. Phase numbers refer to `docs/DEVELOPMENT_PLAN.md`.

| Gap | Change | Where | Effort |
|---|---|---|---|
| Classical descriptors cap visual matching | Add stronger classical image features (colour-layout moments, shape histogram) and ablate on the real product-pair benchmark | `embedders.py`, `ml/eval/real-image-baseline.json` | 3–4 h |
| OCR needs a native binary | `pip install pytesseract` and install Tesseract; add `report_photos` rows so each photo keeps its own OCR text | `lostfound/ocr.py`, migration | 1–2 h + install |
| Notifications only go to the log | Set `SMTP_HOST`/`SMTP_FROM`, add SMS/WhatsApp channels and per-user preferences | `notify.py`, settings | hours + provider account |
| No QR handover | Print a QR of the claim id at the desk; scan to open the collect form | Desk UI | 1 day |
| Multi-campus at scale | Campus-scoped places lookup, onboarding runbook, per-campus taxonomy | Phase 6 | 3–4 days |

## The claim to make in the submission

> The system files lost and found reports from a text description and/or a
> photograph and ranks candidate counterparts using visual, textual, temporal and
> location evidence. A learned re-ranker trained on labelled pairs and held out by
> item improves Recall@1 from 0.74 to 0.85 and nDCG@10 from 0.86 to 0.94; a fitted
> calibration map reduces expected calibration error from 0.35 to 0.005 while
> holding precision at the alert point at 0.90. Recovery is a closed loop:
> the owner answers challenge questions drawn from their own report, receives a
> one-time handover code (stored only as a hash), and staff close the counter —
> which marks both reports recovered and notifies the owner. Encoders are
> classical by design — the system loads and trains no deep model — OCR runs
> only when Tesseract is installed, and notifications default to the local log
> transport.

Do not write "98% accurate", "detects any object", or "automatically returns
items". Every number needs its corpus, query count and spread — see
`docs/EVALUATION.md`.
