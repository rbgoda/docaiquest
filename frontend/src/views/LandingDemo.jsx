// LandingDemo — the interactive "watch it read a document" showcase on the
// landing page. 100% dummy data, no backend, no signup. Demonstrates the key
// selling points: per-field extraction with COLORED BOUNDING BOXES, a user
// YELLOW HIGHLIGHT, per-field confidence, a trust score, and colorful analytics.
//
// Self-contained: its own scoped `lddemo-*` styles + a fixed %-positioned mock
// invoice so the boxes always line up with the values at any size.

import React, { useEffect, useRef, useState } from "react";

// Field families share the FieldOverlay palette so the demo matches the product.
const C = {
  red: "216,98,94", amber: "224,162,59", emerald: "63,164,122",
  gold: "200,160,76", violet: "139,127,214", blue: "104,150,206",
};

// Two documents so the demo proves the "any type" claim with REAL bounding boxes:
// an invoice and a passport read the same way — each field detected + located, each
// scored, and a per-document trust score {classification · OCR quality · field conf}.
const DOCS = {
  invoice: {
    kind: "Invoice", conf: 98,
    trust: { score: 94, level: "High", cls: 99, ocr: 97, field: 98 },
    fields: [
      { k: "vendor",   label: "Vendor",      value: "Acme Supplies Pte Ltd", conf: 0.99, color: C.red,    box: { t: 6.5, l: 5,  w: 49, h: 7 } },
      { k: "invno",    label: "Invoice no.", value: "INV-2042",              conf: 0.99, color: C.gold,   box: { t: 7,   l: 67, w: 28, h: 6 } },
      { k: "issue",    label: "Issue date",  value: "12 Jun 2026",           conf: 0.98, color: C.violet, box: { t: 17,  l: 67, w: 28, h: 5.5 } },
      { k: "due",      label: "Due date",    value: "12 Jul 2026",           conf: 0.97, color: C.violet, box: { t: 24,  l: 67, w: 28, h: 5.5 } },
      { k: "billto",   label: "Bill to",     value: "Globex Holdings",       conf: 0.96, color: C.blue,   box: { t: 21,  l: 5,  w: 42, h: 6 } },
      { k: "subtotal", label: "Subtotal",    value: "$12,400.00",            conf: 0.99, color: C.amber,  box: { t: 71,  l: 60, w: 34, h: 5.5 } },
      { k: "tax",      label: "Tax (9%)",    value: "$1,116.00",             conf: 0.98, color: C.amber,  box: { t: 77,  l: 60, w: 34, h: 5.5 } },
      { k: "total",    label: "Total due",   value: "$13,516.00",            conf: 0.99, color: C.amber,  box: { t: 84,  l: 60, w: 34, h: 6.5 } },
    ],
    highlight: { box: { t: 92, l: 5, w: 56, h: 6 }, text: "Payment terms: Net 30", note: "Net 30 — 2% early-pay discount" },
    ask: { q: "When is this invoice due, and is there a discount?",
           a: <>Due <b>12 Jul 2026</b>; <b>2% early-pay discount</b> on Net 30.</>, cite: "Due date · your highlight" },
  },
  passport: {
    kind: "Passport", conf: 97,
    trust: { score: 96, level: "High", cls: 99, ocr: 95, field: 97 },
    fields: [
      { k: "ppno",    label: "Passport no.",  value: "E1234567",    conf: 0.99, color: C.violet,  box: { t: 13,  l: 58, w: 36, h: 6 } },
      { k: "surname", label: "Surname",       value: "DOE",         conf: 0.99, color: C.red,     box: { t: 27,  l: 40, w: 54, h: 6 } },
      { k: "given",   label: "Given names",   value: "JANE ALICE",  conf: 0.98, color: C.gold,    box: { t: 35,  l: 40, w: 54, h: 6 } },
      { k: "nat",     label: "Nationality",   value: "UTOPIAN",     conf: 0.98, color: C.blue,    box: { t: 43,  l: 40, w: 54, h: 6 } },
      { k: "dob",     label: "Date of birth", value: "14 AUG 1991", conf: 0.97, color: C.emerald, box: { t: 51,  l: 40, w: 32, h: 6 } },
      { k: "sex",     label: "Sex",           value: "F",           conf: 0.99, color: C.emerald, box: { t: 51,  l: 76, w: 18, h: 6 } },
      { k: "exp",     label: "Date of expiry",value: "14 AUG 2031", conf: 0.98, color: C.amber,   box: { t: 59,  l: 40, w: 54, h: 6 } },
    ],
    highlight: { box: { t: 82, l: 5, w: 90, h: 12 }, text: "P<UTODOE<<JANE<ALICE<<<<<<<<<<<<<<<<<<", note: "MRZ checksum verified" },
    ask: { q: "Is this passport still valid, and whose is it?",
           a: <>Valid to <b>14 Aug 2031</b>; holder <b>Jane Alice Doe</b>.</>, cite: "Date of expiry · Surname" },
  },
  bank: {
    kind: "Bank statement", conf: 96, title: "MONTHLY STATEMENT",
    trust: { score: 95, level: "High", cls: 98, ocr: 96, field: 96 },
    rows: [
      ["02 Jun", "Payroll credit", "+$5,120.00"],
      ["05 Jun", "Rent — landlord", "−$1,800.00"],
      ["11 Jun", "Grocery — FreshMart", "−$243.60"],
      ["17 Jun", "Utilities — PowerCo", "−$182.40"],
      ["24 Jun", "Refund — retailer", "+$300.00"],
      ["28 Jun", "Card autopay", "−$1,454.00"],
    ], rowsTop: 27, rowsClass: "txn",
    fields: [
      { k: "holder",  label: "Account holder", value: "Alex Morgan",   conf: 0.99, color: C.red,     box: { t: 8,  l: 5,  w: 44, h: 6 } },
      { k: "acct",    label: "Account no.",     value: "•••• 3390",     conf: 0.99, color: C.gold,    box: { t: 8,  l: 60, w: 34, h: 6 } },
      { k: "period",  label: "Statement period",value: "1–30 Jun 2026", conf: 0.98, color: C.violet,  box: { t: 16, l: 5,  w: 44, h: 5.5 } },
      { k: "credits", label: "Total credits",   value: "+$5,420.00",    conf: 0.98, color: C.emerald, box: { t: 64, l: 58, w: 36, h: 5.5 } },
      { k: "debits",  label: "Total debits",    value: "−$3,680.00",    conf: 0.97, color: C.amber,   box: { t: 71, l: 58, w: 36, h: 5.5 } },
      { k: "closing", label: "Closing balance", value: "$9,980.00",     conf: 0.99, color: C.amber,   box: { t: 79, l: 58, w: 36, h: 6.5 } },
    ],
    highlight: { box: { t: 27, l: 5, w: 52, h: 4.6 }, text: "Payroll +$5,120.00", note: "Largest credit this month" },
    ask: { q: "What came in and out this month, and my balance?",
           a: <>In <b>+$5,420</b>, out <b>−$3,680</b> → closing <b>$9,980.00</b>.</>, cite: "Transactions · Closing balance" },
  },
  nid: {
    kind: "National ID", conf: 97, title: "IDENTITY CARD",
    trust: { score: 96, level: "High", cls: 99, ocr: 96, field: 97 },
    fields: [
      { k: "name",  label: "Full name",     value: "ALEX MORGAN",     conf: 0.99, color: C.red,     box: { t: 20, l: 40, w: 54, h: 6 } },
      { k: "idno",  label: "ID number",     value: "S7654321B",       conf: 0.99, color: C.gold,    box: { t: 29, l: 40, w: 40, h: 6 } },
      { k: "dob",   label: "Date of birth", value: "14 FEB 1985",     conf: 0.98, color: C.violet,  box: { t: 38, l: 40, w: 40, h: 6 } },
      { k: "nat",   label: "Nationality",   value: "SINGAPOREAN",     conf: 0.98, color: C.blue,    box: { t: 47, l: 40, w: 40, h: 6 } },
      { k: "sex",   label: "Sex",           value: "M",               conf: 0.99, color: C.emerald, box: { t: 47, l: 82, w: 12, h: 6 } },
      { k: "addr",  label: "Address",       value: "10 Marina Way",   conf: 0.96, color: C.emerald, box: { t: 56, l: 40, w: 54, h: 6 } },
      { k: "exp",   label: "Expiry",        value: "27 JUL 2029",     conf: 0.97, color: C.amber,   box: { t: 65, l: 40, w: 40, h: 6 } },
    ],
    highlight: { box: { t: 77, l: 40, w: 54, h: 6 }, text: "Checksum valid", note: "ID number format verified" },
    ask: { q: "Whose card is this and what's the ID number?",
           a: <><b>Alex Morgan</b> · ID <b>S7654321B</b> · Singaporean.</>, cite: "Full name · ID number" },
  },
  receipt: {
    kind: "Receipt", conf: 95, title: "FRESH MART",
    trust: { score: 92, level: "High", cls: 97, ocr: 93, field: 95 },
    rows: [
      ["Sourdough loaf", "1", "$6.50"],
      ["Free-range eggs ×12", "1", "$5.20"],
      ["Avocado", "3", "$7.50"],
      ["Oat milk", "2", "$9.00"],
      ["Dark chocolate", "2", "$8.40"],
      ["Cold-brew coffee", "1", "$4.90"],
    ], rowsTop: 26, rowsClass: "items",
    fields: [
      { k: "merchant", label: "Merchant",  value: "Fresh Mart Grocery", conf: 0.99, color: C.red,     box: { t: 8,  l: 18, w: 60, h: 6 } },
      { k: "date",     label: "Date",      value: "22 Jun 2026",        conf: 0.98, color: C.violet,  box: { t: 16, l: 5,  w: 44, h: 5.5 } },
      { k: "sub",      label: "Subtotal",  value: "$41.50",             conf: 0.98, color: C.blue,    box: { t: 63, l: 58, w: 36, h: 5.5 } },
      { k: "tax",      label: "GST 9%",    value: "$3.74",              conf: 0.97, color: C.amber,   box: { t: 70, l: 58, w: 36, h: 5.5 } },
      { k: "total",    label: "Total",     value: "$45.24",             conf: 0.99, color: C.amber,   box: { t: 78, l: 58, w: 36, h: 6.5 } },
      { k: "pay",      label: "Paid with", value: "Visa •••• 1194",     conf: 0.96, color: C.emerald, box: { t: 89, l: 5,  w: 48, h: 5.5 } },
    ],
    highlight: { box: { t: 26, l: 5, w: 52, h: 4.6 }, text: "6 line items", note: "Every item captured" },
    ask: { q: "How much did I spend and what's on it?",
           a: <><b>$45.24</b> at Fresh Mart — <b>6 items</b> incl. GST.</>, cite: "Total · line items" },
  },
  resume: {
    kind: "Resume", conf: 96, title: "CURRICULUM VITAE",
    trust: { score: 93, level: "High", cls: 98, ocr: 95, field: 96 },
    fields: [
      { k: "name",   label: "Name",          value: "Alex Morgan",           conf: 0.99, color: C.red,     box: { t: 8,  l: 5,  w: 50, h: 6.5 } },
      { k: "title",  label: "Current title", value: "Product Manager",       conf: 0.97, color: C.gold,    box: { t: 16, l: 5,  w: 54, h: 5.5 } },
      { k: "email",  label: "Email",         value: "•••@•••.com",           conf: 0.98, color: C.violet,  box: { t: 16, l: 62, w: 32, h: 5.5 } },
      { k: "exp",    label: "Experience",    value: "12+ years",             conf: 0.96, color: C.blue,    box: { t: 34, l: 5,  w: 34, h: 5.5 } },
      { k: "roles",  label: "Work history",  value: "5 roles",               conf: 0.95, color: C.emerald, box: { t: 44, l: 5,  w: 40, h: 5.5 } },
      { k: "skills", label: "Skills",        value: "14 listed",             conf: 0.95, color: C.emerald, box: { t: 60, l: 5,  w: 44, h: 5.5 } },
      { k: "edu",    label: "Education",     value: "2 qualifications",      conf: 0.96, color: C.amber,   box: { t: 76, l: 5,  w: 46, h: 5.5 } },
    ],
    highlight: { box: { t: 34, l: 44, w: 50, h: 5.5 }, text: "Product & data analytics", note: "Experience captured as a list" },
    ask: { q: "How senior is this candidate and what's their focus?",
           a: <><b>12+ years</b>, Product Manager — product & analytics.</>, cite: "Experience · Current title" },
  },
  card: {
    kind: "Credit-card statement", conf: 96, title: "CREDIT CARD STATEMENT",
    trust: { score: 94, level: "High", cls: 98, ocr: 95, field: 96 },
    rows: [
      ["03 Jun", "Amazon.com", "$96.40"],
      ["07 Jun", "Shell fuel", "$68.00"],
      ["14 Jun", "Trattoria — dinner", "$142.20"],
      ["19 Jun", "Netflix", "$15.99"],
      ["23 Jun", "SkyJet — flights", "$1,458.00"],
      ["27 Jun", "FreshMart grocery", "$165.01"],
    ], rowsTop: 27, rowsClass: "txn",
    fields: [
      { k: "holder", label: "Cardholder",     value: "A MORGAN",    conf: 0.99, color: C.red,     box: { t: 8,  l: 5,  w: 40, h: 6 } },
      { k: "cardno", label: "Card no.",       value: "•••• 5063",   conf: 0.99, color: C.gold,    box: { t: 8,  l: 60, w: 34, h: 6 } },
      { k: "stmt",   label: "Statement date", value: "5 Jun 2026",  conf: 0.98, color: C.violet,  box: { t: 16, l: 5,  w: 44, h: 5.5 } },
      { k: "bal",    label: "New balance",    value: "$1,945.60",   conf: 0.99, color: C.amber,   box: { t: 66, l: 58, w: 36, h: 6.5 } },
      { k: "min",    label: "Minimum due",    value: "$95.00",      conf: 0.98, color: C.blue,    box: { t: 74, l: 58, w: 36, h: 5.5 } },
      { k: "due",    label: "Payment due",    value: "25 Jun 2026", conf: 0.98, color: C.emerald, box: { t: 81, l: 58, w: 36, h: 5.5 } },
    ],
    highlight: { box: { t: 45.5, l: 5, w: 52, h: 4.6 }, text: "SkyJet flights $1,458.00", note: "Largest charge this cycle" },
    ask: { q: "What did I spend on my card and what's due?",
           a: <>6 charges → <b>$1,945.60</b> due by <b>25 Jun</b> (min $95).</>, cite: "Transactions · New balance" },
  },
  license: {
    kind: "Driver's licence", conf: 96, title: "DRIVING LICENCE",
    trust: { score: 95, level: "High", cls: 98, ocr: 95, field: 96 },
    fields: [
      { k: "name",  label: "Name",         value: "SARAH J LEE", conf: 0.99, color: C.red,     box: { t: 18, l: 40, w: 54, h: 6 } },
      { k: "licno", label: "Licence no.",  value: "S1234567X",   conf: 0.99, color: C.gold,    box: { t: 27, l: 40, w: 40, h: 6 } },
      { k: "dob",   label: "Date of birth",value: "05 MAR 1990", conf: 0.98, color: C.violet,  box: { t: 36, l: 40, w: 40, h: 6 } },
      { k: "class", label: "Class",        value: "3 · Auto",    conf: 0.98, color: C.blue,    box: { t: 45, l: 40, w: 30, h: 6 } },
      { k: "issue", label: "Issued",       value: "10 JAN 2020", conf: 0.97, color: C.emerald, box: { t: 54, l: 40, w: 40, h: 6 } },
      { k: "exp",   label: "Expiry",       value: "10 JAN 2030", conf: 0.98, color: C.amber,   box: { t: 63, l: 40, w: 40, h: 6 } },
    ],
    highlight: { box: { t: 74, l: 40, w: 54, h: 6 }, text: "No endorsements", note: "Restrictions checked" },
    ask: { q: "Is this licence valid and what can they drive?",
           a: <>Valid to <b>10 Jan 2030</b> · <b>Class 3</b> (automatic cars).</>, cite: "Expiry · Class" },
  },
  insurance: {
    kind: "Insurance policy", conf: 95, title: "POLICY SCHEDULE",
    trust: { score: 93, level: "High", cls: 97, ocr: 94, field: 95 },
    fields: [
      { k: "insured", label: "Insured",   value: "Alex Morgan",    conf: 0.99, color: C.red,     box: { t: 10, l: 5,  w: 46, h: 6 } },
      { k: "polno",   label: "Policy no.",value: "POL-99823",      conf: 0.99, color: C.gold,    box: { t: 10, l: 60, w: 34, h: 6 } },
      { k: "period",  label: "Period",    value: "2026 – 2027",    conf: 0.97, color: C.violet,  box: { t: 20, l: 5,  w: 46, h: 5.5 } },
      { k: "cover",   label: "Coverage",  value: "$500,000",       conf: 0.98, color: C.amber,   box: { t: 40, l: 58, w: 36, h: 6.5 } },
      { k: "premium", label: "Premium",   value: "$1,240 / yr",    conf: 0.98, color: C.blue,    box: { t: 49, l: 58, w: 36, h: 5.5 } },
      { k: "insurer", label: "Insurer",   value: "Acme Assurance", conf: 0.98, color: C.emerald, box: { t: 50, l: 5,  w: 48, h: 5.5 } },
    ],
    highlight: { box: { t: 66, l: 5, w: 54, h: 6 }, text: "Excludes flood damage", note: "Key exclusion flagged" },
    ask: { q: "How much am I covered for and what's the premium?",
           a: <><b>$500,000</b> cover · <b>$1,240/yr</b> — excludes flood.</>, cite: "Coverage · Premium" },
  },
  lab: {
    kind: "Lab report", conf: 96, title: "LABORATORY REPORT",
    trust: { score: 94, level: "High", cls: 98, ocr: 95, field: 96 },
    rows: [
      ["Glucose (fasting)", "98 mg/dL", "70–99"],
      ["Total cholesterol", "212 mg/dL", "< 200"],
      ["HDL cholesterol", "48 mg/dL", "> 40"],
      ["LDL cholesterol", "138 mg/dL", "< 130"],
      ["Vitamin D", "18 ng/mL", "30–100"],
      ["Hemoglobin", "14.2 g/dL", "13–17"],
    ], rowsTop: 30, rowsClass: "txn",
    fields: [
      { k: "patient", label: "Patient",     value: "Alex Morgan",           conf: 0.99, color: C.red,     box: { t: 8,   l: 5,  w: 44, h: 6 } },
      { k: "lab",     label: "Lab",         value: "Meridian Diagnostics",  conf: 0.98, color: C.violet,  box: { t: 8,   l: 58, w: 36, h: 6 } },
      { k: "coll",    label: "Collected",   value: "26 Jun 2026",           conf: 0.98, color: C.gold,    box: { t: 16,  l: 5,  w: 44, h: 5.5 } },
      { k: "glucose", label: "Glucose",     value: "98 mg/dL",              conf: 0.98, color: C.emerald, box: { t: 30,  l: 40, w: 26, h: 4.4 } },
      { k: "chol",    label: "Cholesterol", value: "212 mg/dL",             conf: 0.97, color: C.amber,   box: { t: 34.6, l: 40, w: 26, h: 4.4 } },
      { k: "vitd",    label: "Vitamin D",   value: "18 ng/mL",              conf: 0.97, color: C.red,     box: { t: 48.4, l: 40, w: 26, h: 4.4 } },
    ],
    highlight: { box: { t: 48.4, l: 5, w: 30, h: 4.4 }, text: "Vitamin D 18 — LOW", note: "Below reference range" },
    ask: { q: "Any result out of range?",
           a: <><b>Vitamin D is low</b> (18 ng/mL); LDL borderline — the rest are normal.</>, cite: "Vitamin D · reference range" },
  },
  finreport: {
    kind: "Financial report", conf: 96, title: "PORTFOLIO STATEMENT",
    trust: { score: 94, level: "High", cls: 98, ocr: 95, field: 95 },
    rows: [
      ["Equities", "$48,200", "+6.2%"],
      ["Bonds", "$22,500", "+1.1%"],
      ["Cash", "$8,000", "—"],
      ["Crypto", "$5,300", "−12.4%"],
      ["REITs", "$4,100", "+2.8%"],
    ], rowsTop: 32, rowsClass: "txn",
    fields: [
      { k: "holder", label: "Account holder", value: "Alex Morgan", conf: 0.99, color: C.red,     box: { t: 9,   l: 5,  w: 44, h: 6 } },
      { k: "period", label: "Period",         value: "Q2 2026",     conf: 0.98, color: C.violet,  box: { t: 17,  l: 5,  w: 36, h: 5.5 } },
      { k: "top",    label: "Top holding",    value: "Equities",    conf: 0.96, color: C.blue,    box: { t: 32,  l: 5,  w: 30, h: 4.4 } },
      { k: "total",  label: "Total value",    value: "$88,100",     conf: 0.99, color: C.amber,   box: { t: 66,  l: 58, w: 36, h: 6.5 } },
      { k: "gain",   label: "Net gain",       value: "+3.8%",       conf: 0.97, color: C.emerald, box: { t: 74,  l: 58, w: 36, h: 5.5 } },
    ],
    highlight: { box: { t: 46, l: 5, w: 34, h: 4.4 }, text: "Crypto −12.4%", note: "Worst performer this quarter" },
    ask: { q: "How's my portfolio doing?",
           a: <>Up <b>+3.8%</b> to <b>$88,100</b> — equities lead, crypto down 12%.</>, cite: "Total value · Net gain" },
  },
  accounting: {
    kind: "Accounting · P&L", conf: 97, title: "PROFIT & LOSS",
    trust: { score: 95, level: "High", cls: 98, ocr: 96, field: 96 },
    rows: [
      ["Revenue", "", "$142,000"],
      ["Cost of goods sold", "", "−$61,000"],
      ["Gross profit", "", "$81,000"],
      ["Operating expenses", "", "−$44,500"],
      ["Net income", "", "$36,500"],
    ], rowsTop: 34, rowsClass: "txn",
    fields: [
      { k: "entity",  label: "Entity",     value: "Northwind Ltd", conf: 0.99, color: C.red,     box: { t: 9,   l: 5,  w: 46, h: 6 } },
      { k: "period",  label: "Period",     value: "FY 2026",       conf: 0.98, color: C.violet,  box: { t: 17,  l: 5,  w: 40, h: 5.5 } },
      { k: "revenue", label: "Revenue",    value: "$142,000",      conf: 0.98, color: C.blue,    box: { t: 34,  l: 62, w: 32, h: 4.6 } },
      { k: "net",     label: "Net income", value: "$36,500",       conf: 0.99, color: C.emerald, box: { t: 52.4, l: 62, w: 32, h: 5 } },
      { k: "margin",  label: "Net margin", value: "25.7%",         conf: 0.97, color: C.amber,   box: { t: 72,  l: 58, w: 36, h: 5.5 } },
    ],
    highlight: { box: { t: 52.4, l: 5, w: 40, h: 5 }, text: "Net income $36,500", note: "Net margin 25.7%" },
    ask: { q: "What's the net income and margin?",
           a: <>Net income <b>$36,500</b> on $142k revenue — <b>25.7% margin</b>.</>, cite: "Net income · Revenue" },
  },
  dashboard: {
    kind: "Analytics dashboard", conf: 0, isDash: true,
  },
  chat: {
    kind: "Cross-doc chat", conf: 0, isChat: true,
  },
};
const DOC_KEYS = [
  ["invoice", "🧾 Invoice"], ["passport", "🛂 Passport"], ["bank", "🏦 Bank statement"],
  ["card", "💳 Card statement"], ["nid", "🪪 National ID"], ["license", "🚗 Driver licence"],
  ["receipt", "🧾 Receipt"], ["resume", "📄 Resume"], ["insurance", "🛡️ Insurance"],
  ["lab", "🧪 Lab report"], ["finreport", "📈 Financial report"], ["accounting", "🧮 P&L / accounting"],
  ["dashboard", "📊 Analytics dashboard"], ["chat", "💬 Chat across your docs"],
];

// The chat example — how DocAIQ answers ACROSS your own documents, with citations.
// All figures are illustrative dummy data.
const CHAT_TURNS = [
  { role: "user", text: "What's my combined invoice total?" },
  { role: "ai", text: <>Your <b>5 invoices</b> add up to <b>$25,340.00</b>.</>,
    table: [["Northwind Traders", "$4,200.00"], ["Globex Corp", "$3,850.00"], ["Initech LLC", "$12,600.00"], ["Umbrella Inc", "$3,700.00"], ["Hooli", "$990.00"]],
    cite: "5 invoices · Total field" },
  { role: "user", text: "How much do I owe on my credit card?" },
  { role: "ai", text: <><b>$1,945.60</b>, due <b>25 Jun 2026</b> — from your card statement, not your invoices.</>,
    cite: "Credit-card statement · New balance" },
  { role: "user", text: "Compare my two résumés — what's different?" },
  { role: "ai", text: <>Same person, different emphasis:</>,
    diff: [["Experience", "5 roles", "3 roles"], ["Skills", "14", "8"], ["Headline", "Product Manager", "Data Analyst"]],
    cite: "2 résumés · field-by-field" },
];

// Top-10 capability carousel — benefit-level only (no internals / competitors).
const CAPS = [
  { icon: "📥", title: "Universal extraction", blurb: "Any document type → structured fields, each with a confidence score.", color: C.blue,    viz: "boxes" },
  { icon: "🏷️", title: "Auto-classify & tag", blurb: "Every upload is typed and organized on arrival — new shapes just work.", color: C.gold,    viz: "boxes" },
  { icon: "🎯", title: "Field location & highlights", blurb: "Every value pinned on the page with a colored box you can click.", color: C.red,     viz: "highlight" },
  { icon: "💬", title: "Cited cross-doc chat", blurb: "Ask across your whole library; every answer cites the source.", color: C.violet,  viz: "chat" },
  { icon: "✍️", title: "Draw highlights, then ask", blurb: "Mark a region or add a note — the assistant grounds answers on it.", color: C.amber,   viz: "highlight" },
  { icon: "🛡️", title: "Trust score", blurb: "A per-document score fuses classification, OCR quality & field confidence.", color: C.emerald, viz: "donut" },
  { icon: "📊", title: "Tables & figures", blurb: "Multi-page tables and charts pulled into clean, structured data.", color: C.blue,    viz: "table" },
  { icon: "📤", title: "Export to CSV / Excel", blurb: "Turn a folder of documents into a spreadsheet in one ask.", color: C.gold,    viz: "table" },
  { icon: "🔀", title: "Compare & summarize", blurb: "Side-by-side comparisons and role-aware summaries — without opening the file.", color: C.violet,  viz: "chat" },
  { icon: "🔐", title: "Privacy-native & PII-safe", blurb: "Files stay in your own Drive; sensitive IDs masked before any AI sees them.", color: C.red,     viz: "shield" },
];

// Small feature-specific illustrations (reused across slides).
function MiniViz({ type, color }) {
  const rgb = `rgb(${color})`, soft = `rgba(${color},0.18)`;
  if (type === "boxes") return (
    <div className="lddemo-mv lddemo-mv-doc">
      {[[18, 30], [40, 55], [62, 42]].map(([t, w], i) => (
        <div key={i} style={{ position: "absolute", top: `${t}%`, left: "12%", width: `${w}%`, height: "12%",
          border: `2px solid ${rgb}`, background: soft, borderRadius: 3 }} />))}
    </div>);
  if (type === "highlight") return (
    <div className="lddemo-mv lddemo-mv-doc">
      <div style={{ position: "absolute", top: "30%", left: "12%", width: "62%", height: "14%", background: "rgba(245,210,70,.34)", border: "2px solid rgba(224,162,59,.85)", borderRadius: 3 }} />
      <div style={{ position: "absolute", top: "26%", left: "78%", width: "16%", height: "22%", background: "#1c1f27", border: "1px solid rgba(224,162,59,.5)", borderRadius: 4 }} />
      <div style={{ position: "absolute", top: "58%", left: "12%", width: "45%", height: "8%", background: soft, border: `2px solid ${rgb}`, borderRadius: 3 }} />
    </div>);
  if (type === "chat") return (
    <div className="lddemo-mv lddemo-mv-chat">
      <div className="lddemo-bub q">When is this due?</div>
      <div className="lddemo-bub a" style={{ borderColor: rgb }}>12 Jul 2026 <span style={{ color: rgb }}>[cite]</span></div>
    </div>);
  if (type === "donut") return (
    <div className="lddemo-mv lddemo-mv-c"><Donut pct={94} label="Trust" /></div>);
  if (type === "table") return (
    <div className="lddemo-mv lddemo-mv-c">
      <div className="lddemo-minitbl" style={{ "--mc": rgb }}>
        {Array.from({ length: 9 }).map((_, i) => <span key={i} style={i % 3 === 2 ? { color: rgb, fontWeight: 700 } : undefined}>{i % 3 === 2 ? "$" : "▪"}</span>)}
      </div>
    </div>);
  return ( // shield
    <div className="lddemo-mv lddemo-mv-c">
      <svg viewBox="0 0 48 56" width="58" height="64"><path d="M24 2 L44 10 V28 C44 42 35 50 24 54 C13 50 4 42 4 28 V10 Z" fill={soft} stroke={rgb} strokeWidth="2.5"/><path d="M16 28 l6 6 l11 -13" fill="none" stroke={rgb} strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round"/></svg>
    </div>);
}

function Donut({ pct = 94, label = "Trust score" }) {
  const r = 26, c = 2 * Math.PI * r, off = c * (1 - pct / 100);
  return (
    <div className="lddemo-donut">
      <svg viewBox="0 0 64 64" width="64" height="64">
        <circle cx="32" cy="32" r={r} fill="none" stroke="rgba(255,255,255,.10)" strokeWidth="7" />
        <circle cx="32" cy="32" r={r} fill="none" stroke="rgba(63,164,122,.95)" strokeWidth="7"
                strokeDasharray={c} strokeDashoffset={off} strokeLinecap="round"
                transform="rotate(-90 32 32)" />
        <text x="32" y="36" textAnchor="middle" fontSize="15" fill="currentColor" fontWeight="700">{pct}</text>
      </svg>
      <span>{label}</span>
    </div>
  );
}

// Trust-score breakdown — the hero feature as a live element: the fused score +
// the three signals it's built from (classification · scan quality · field conf).
function TrustCard({ trust }) {
  const rows = [["Classification", trust.cls], ["Scan / OCR quality", trust.ocr], ["Field confidence", trust.field]];
  return (
    <div className="lddemo-trust">
      <Donut pct={trust.score} label={`Trust · ${trust.level}`} />
      <div className="lddemo-treasons">
        {rows.map(([l, v]) => (
          <div className="lddemo-treason" key={l}>
            <span className="lddemo-trl">{l}</span>
            <span className="lddemo-trbar"><span style={{ width: `${v}%` }} /></span>
            <span className="lddemo-trv">{v}%</span>
          </div>
        ))}
        <div className="lddemo-tnote">Fused into one score — so you know which docs to auto-approve and which to check.</div>
      </div>
    </div>
  );
}

// Phase 2b showcase — "correct once, it learns". Auto-loops: AI flags a
// low-confidence field (wrong line) → one correction snaps the box to the right
// place + verifies it → DocAIQ learns (anonymized pattern only).
function CorrectDemo() {
  const [phase, setPhase] = useState(0); // 0 uncertain · 1 corrected · 2 learned
  const cp = useRef(false);
  useEffect(() => {
    const id = setInterval(() => { if (!cp.current) setPhase((p) => (p + 1) % 3); }, 2600);
    return () => clearInterval(id);
  }, []);
  const corrected = phase >= 1;
  return (
    <div className="lddemo-correct"
         onMouseEnter={() => { cp.current = true; }}
         onMouseLeave={() => { cp.current = false; }}>
      <div className="ld-kicker" style={{ marginTop: 36 }}>Human-in-the-loop · correct once, it learns</div>
      <div className="lddemo-cgrid">
        <div className="lddemo-cpaper">
          <div className="lddemo-crow" style={{ top: "16%" }}><span>Subtotal</span><span>$12,400.00</span></div>
          <div className="lddemo-crow" style={{ top: "36%" }}><span>Tax (9%)</span><span>$1,116.00</span></div>
          <div className="lddemo-crow tot" style={{ top: "60%" }}><span>TOTAL</span><span>$13,516.00</span></div>
          <div className="lddemo-cbox" style={{
            top: corrected ? "57.5%" : "13.5%",
            borderColor: corrected ? "rgba(63,164,122,.95)" : "rgba(224,162,59,.9)",
            background: corrected ? "rgba(63,164,122,.16)" : "rgba(224,162,59,.16)",
          }}>
            <span className="lddemo-cbadge" style={{ color: corrected ? "#7fd6ab" : "#e6b25a" }}>
              {corrected ? "✓ Total · 100%" : "⚠ Total? · 62%"}
            </span>
          </div>
        </div>
        <div className="lddemo-ctxt">
          <div className="lddemo-csteps">
            {["AI flags a low-confidence field", "You draw the right box — one fix", "DocAIQuest learns it — next time it's automatic"].map((t, i) => (
              <div key={i} className={`lddemo-cstep${phase === i ? " on" : ""}`}><b>{i + 1}</b> {t}</div>
            ))}
          </div>
          <p className="ld-lead" style={{ margin: "4px 0 0" }}>
            The AI first read <b>Total</b> as the <span className="lddemo-was">subtotal</span> at 62% confidence.
            One correction snaps the box to the right line, sets the value, and marks it <b>verified</b>.
          </p>
          {phase === 2 && (
            <div className="lddemo-learned">✓ Learned — future invoices of this type auto-correct.
              <span> Only an anonymized pattern is shared (opt-in); your values never leave your Drive.</span></div>
          )}
          <button className="ld-btn ld-btn-primary" style={{ marginTop: 14 }} onClick={() => setPhase(1)}>Fix it →</button>
        </div>
      </div>
    </div>
  );
}

// The cross-doc chat example — how DocAIQ answers ACROSS your own documents, with citations.
function ChatDemo() {
  return (
    <div className="lddemo-chatdemo">
      <div className="lddemo-chatpanel">
        <div className="lddemo-chathead"><span className="lddemo-chatdot" /> Ask across your documents</div>
        <div className="lddemo-chatbody">
          {CHAT_TURNS.map((t, i) => (
            <div key={i} className={`lddemo-msg ${t.role}`}>
              <div className="lddemo-bubble">
                <div>{t.text}</div>
                {t.table && (
                  <table className="lddemo-mtable"><tbody>
                    {t.table.map((r, j) => (<tr key={j}><td>{r[0]}</td><td className="num">{r[1]}</td></tr>))}
                  </tbody></table>
                )}
                {t.diff && (
                  <table className="lddemo-mtable diff">
                    <thead><tr><th></th><th>Résumé A</th><th>Résumé B</th></tr></thead>
                    <tbody>{t.diff.map((r, j) => (<tr key={j}><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td></tr>))}</tbody>
                  </table>
                )}
                {t.cite && <div className="lddemo-mcite">📎 {t.cite}</div>}
              </div>
            </div>
          ))}
        </div>
        <div className="lddemo-chatfoot">Answers come only from your own files — every one cited. No match, it says so rather than guess.</div>
      </div>
      <div className="lddemo-chatside">
        <div className="lddemo-chatside-h">Why this chat is different</div>
        {[["🔗", "Across everything", "One question spans all your documents — totals, comparisons, counts."],
          ["📎", "Always cited", "Each answer points back to the exact source document and field."],
          ["🙊", "Honest by default", "No document, no guess — it tells you when it can't find something."],
          ["🔒", "Private", "Answers come from your own Drive; your files are never used to train."]].map(([ic, h, p]) => (
            <div className="lddemo-chatcap" key={h}><span>{ic}</span><div><b>{h}</b><p>{p}</p></div></div>
          ))}
      </div>
    </div>
  );
}

// Analytics-dashboard demo — a live dashboard built from the demo documents.
function DashDemo() {
  const kpis = [
    ["Income", "$8,240", C.emerald], ["Spend", "$5,110", C.amber],
    ["Saved", "+$3,130", C.blue], ["Documents", "31", C.gold],
  ];
  const bars = [62, 40, 78, 34, 90, 52, 70, 48];
  const cats = [
    ["Dining", 92, "$1,420", C.red], ["Groceries", 74, "$1,140", C.emerald],
    ["Transport", 55, "$860", C.blue], ["Bills", 48, "$740", C.violet],
    ["Shopping", 40, "$620", C.amber],
  ];
  const merchants = [["SkyJet — flights", "$1,458"], ["FreshMart grocery", "$165"], ["Trattoria dinner", "$142"], ["Shell fuel", "$68"]];
  return (
    <div className="ldd-dash">
      <div className="ldd-dhead">
        <div>
          <div className="ldd-dtitle">Financial dashboard</div>
          <div className="ldd-dsub">Built live from 31 of your documents — no manual entry</div>
        </div>
        <span className="lddemo-pill ok">auto-generated</span>
      </div>
      <div className="ldd-kpis">
        {kpis.map(([l, v, c]) => (
          <div className="ldd-kpi" key={l} style={{ "--c": `rgb(${c})` }}>
            <span className="ldd-kl">{l}</span><span className="ldd-kv">{v}</span>
          </div>
        ))}
      </div>
      <div className="ldd-drow">
        <div className="ldd-card">
          <div className="ldd-ctitle">Spend by month</div>
          <div className="ldd-bars">
            {bars.map((h, i) => <span key={i} style={{ height: `${h}%`, background: i === 4 ? `rgb(${C.gold})` : "rgba(255,255,255,.12)" }} />)}
          </div>
        </div>
        <div className="ldd-card">
          <div className="ldd-ctitle">Where it goes</div>
          {cats.map(([l, w, v, c]) => (
            <div className="ldd-cat" key={l}>
              <span className="ldd-catl">{l}</span>
              <span className="ldd-catbar"><span style={{ width: `${w}%`, background: `rgb(${c})` }} /></span>
              <span className="ldd-catv">{v}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="ldd-ai"><span className="ldd-aidot">✦</span> Spending fell <b>12%</b> vs last month; dining is your top category, and one flight drove the spike.</div>
      <div className="ldd-card">
        <div className="ldd-ctitle">Top merchants</div>
        <div className="ldd-mgrid">
          {merchants.map(([m, v]) => <div className="ldd-mrow" key={m}><span>{m}</span><span>{v}</span></div>)}
        </div>
      </div>
    </div>
  );
}

export default function LandingDemo() {
  const [docKey, setDocKey] = useState("invoice");
  const [focus, setFocus] = useState(0);
  const paused = useRef(false);
  const [slide, setSlide] = useState(0);
  const cpaused = useRef(false);
  const D = DOCS[docKey];
  const FIELDS = D.fields || [];
  const HL = D.highlight;
  const switchDoc = (k) => { setDocKey(k); setFocus(0); };

  // Auto-cycle the focused field so the boxes "light up" on their own.
  useEffect(() => {
    const n = (DOCS[docKey].fields || []).length;
    if (!n) return;                       // chat example has no fields to cycle
    const id = setInterval(() => {
      if (paused.current) return;
      setFocus((i) => (i + 1) % n);
    }, 2000);
    return () => clearInterval(id);
  }, [docKey]);
  // Auto-advance the capability carousel (pause on hover).
  useEffect(() => {
    const id = setInterval(() => {
      if (cpaused.current) return;
      setSlide((s) => (s + 1) % CAPS.length);
    }, 4200);
    return () => clearInterval(id);
  }, []);

  return (
    <section id="demo" className="ld-sec lddemo-sec">
      <style>{LDDEMO_CSS}</style>
      <div className="ld-wrap">
        <div className="ld-kicker">See it in action · no signup</div>
        <h2 className="ld-h2">Watch DocAIQuest <span className="ld-gold">read a document.</span></h2>

        {/* document-type switcher — proves "any type" with real boxes on each, + a cross-doc chat */}
        <div className="lddemo-switch" role="tablist" aria-label="Demo document">
          {DOC_KEYS.map(([k, label]) => (
            <button key={k} role="tab" aria-selected={docKey === k}
                    className={`lddemo-swbtn${docKey === k ? " on" : ""}${k === "chat" ? " chat" : ""}`}
                    onClick={() => switchDoc(k)}>{label}</button>
          ))}
          <span className="lddemo-swhint">← try any</span>
        </div>

        {D.isChat ? <ChatDemo /> : D.isDash ? <DashDemo /> : (
        <div className="lddemo-grid"
             onMouseEnter={() => { paused.current = true; }}
             onMouseLeave={() => { paused.current = false; }}>

          {/* ── the document + bbox overlay ── */}
          <div className="lddemo-doc">
            <div className="lddemo-paper">
              {/* decorative scaffolding — per document type (static) */}
              {D.title && !["invoice", "passport"].includes(docKey) && (
                <div className="lddemo-title">{D.title}</div>
              )}
              {/* transaction / line-item table (bank, card, receipt) */}
              {D.rows && (<>
                <div className="lddemo-rule" style={{ top: `${D.rowsTop - 2.5}%` }} />
                {D.rows.map((r, i) => (
                  <div className={`lddemo-txnrow ${D.rowsClass}`} style={{ top: `${D.rowsTop + i * 4.6}%` }} key={i}>
                    <span>{r[0]}</span><span>{r[1]}</span><span>{r[2]}</span>
                  </div>
                ))}
                <div className="lddemo-rule" style={{ top: `${D.rowsTop + D.rows.length * 4.6 + 0.5}%` }} />
              </>)}
              {docKey === "invoice" && (<>
                <div className="lddemo-title">INVOICE</div>
                <div className="lddemo-sub" style={{ top: "14%", left: "5%" }}>BILL TO</div>
                <div className="lddemo-rule" style={{ top: "37%" }} />
                <div className="lddemo-th" style={{ top: "39%" }}>
                  <span>Description</span><span>Qty</span><span>Amount</span>
                </div>
                {[["Premium widgets", "40", "$8,000.00"], ["Onboarding & setup", "1", "$3,200.00"], ["Support (annual)", "1", "$1,200.00"]].map((r, i) => (
                  <div className="lddemo-tr" style={{ top: `${45 + i * 6.5}%` }} key={i}>
                    <span>{r[0]}</span><span>{r[1]}</span><span>{r[2]}</span>
                  </div>
                ))}
                <div className="lddemo-rule" style={{ top: "68%" }} />
              </>)}
              {docKey === "passport" && (<>
                <div className="lddemo-pptitle">PASSPORT</div>
                <div className="lddemo-ppcountry">REPUBLIC OF UTOPIA · PASSEPORT</div>
                <div className="lddemo-photo" />
                <div className="lddemo-rule" style={{ top: "80%" }} />
              </>)}

              {/* extracted values, each wrapped in its detected bounding box */}
              {FIELDS.map((f, i) => (
                <div key={f.k}
                     className={`lddemo-box${focus === i ? " on" : ""}`}
                     onMouseEnter={() => setFocus(i)}
                     style={{
                       top: `${f.box.t}%`, left: `${f.box.l}%`, width: `${f.box.w}%`, height: `${f.box.h}%`,
                       "--bc": f.color,
                       background: `rgba(${f.color},${focus === i ? 0.22 : 0.10})`,
                       borderColor: `rgba(${f.color},${focus === i ? 1 : 0.7})`,
                     }}>
                    <span className="lddemo-val" style={{ fontWeight: f.k === "total" ? 800 : 600 }}>{f.value}</span>
                </div>
              ))}

              {/* the user's yellow highlight + note tag */}
              <div className={`lddemo-hl${docKey === "passport" ? " mrz" : ""}`}
                   style={{ top: `${HL.box.t}%`, left: `${HL.box.l}%`, width: `${HL.box.w}%`, height: `${HL.box.h}%` }}>
                <span>{HL.text}</span>
              </div>
              <div className="lddemo-note" style={{ top: `${HL.box.t - 1}%`, left: `${Math.min(HL.box.l + HL.box.w + 1, 62)}%` }}>
                📌 {HL.note}
              </div>
            </div>
          </div>

          {/* ── trust score + extracted-fields panel ── */}
          <div className="lddemo-side">
            <TrustCard trust={D.trust} />
            <div className="lddemo-fhead">
              <span>Extracted fields</span>
              <span className="lddemo-pill ok">{D.kind.toLowerCase()} · {D.conf}% conf</span>
            </div>
            <div className="lddemo-list">
              {FIELDS.map((f, i) => (
                <button key={f.k} className={`lddemo-row${focus === i ? " on" : ""}`}
                        onMouseEnter={() => setFocus(i)} onClick={() => setFocus(i)}>
                  <span className="lddemo-chip" style={{ background: `rgb(${f.color})` }} />
                  <span className="lddemo-flabel">{f.label}</span>
                  <span className="lddemo-fval">{f.value}</span>
                  <span className={`lddemo-conf${f.conf >= 0.97 ? " hi" : ""}`}>{Math.round(f.conf * 100)}%</span>
                </button>
              ))}
            </div>
            {/* line-item / transaction extraction — the repeating rows pulled into a structured array */}
            {D.rows && (
              <div className="lddemo-lix">
                <div className="lddemo-fhead">
                  <span>{docKey === "receipt" ? "Line items" : "Transactions"} — extracted</span>
                  <span className="lddemo-pill ok">{D.rows.length} rows · array</span>
                </div>
                <div className="lddemo-litbl">
                  {D.rows.map((r, i) => (
                    <div className={`lddemo-litrow ${D.rowsClass}`} key={i}>
                      <span>{r[0]}</span><span>{r[1]}</span><span>{r[2]}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div className="lddemo-ask">
              <span className="lddemo-q">“{D.ask.q}”</span>
              <span className="lddemo-a">{D.ask.a} <em className="lddemo-cite">[{D.ask.cite}]</em></span>
            </div>
          </div>
        </div>
        )}

        {/* ── colorful analytics strip ── */}
        <div className="lddemo-stats">
          <div className="lddemo-stat s1"><div className="lddemo-snum">12,480</div><div className="lddemo-slab">Documents processed</div></div>
          <div className="lddemo-stat s2"><div className="lddemo-snum">98.2%</div><div className="lddemo-slab">Avg field accuracy</div></div>
          <div className="lddemo-stat s3"><div className="lddemo-snum">100+</div><div className="lddemo-slab">Document types</div></div>
          <div className="lddemo-stat s4"><div className="lddemo-snum">1.4s</div><div className="lddemo-slab">Avg time to answer</div></div>
          <div className="lddemo-stat s5"><Donut pct={94} /></div>
        </div>

        {/* ── Top-10 capability carousel ── */}
        <div className="lddemo-carousel"
             onMouseEnter={() => { cpaused.current = true; }}
             onMouseLeave={() => { cpaused.current = false; }}>
          <div className="lddemo-cartop">
            <span className="ld-kicker" style={{ margin: 0 }}>Top 10 capabilities</span>
            <div className="lddemo-arrows">
              <button onClick={() => setSlide((s) => (s - 1 + CAPS.length) % CAPS.length)} aria-label="Previous">‹</button>
              <button onClick={() => setSlide((s) => (s + 1) % CAPS.length)} aria-label="Next">›</button>
            </div>
          </div>
          <div className="lddemo-card" style={{ "--ac": `rgb(${CAPS[slide].color})` }}>
            <div className="lddemo-cardviz"><MiniViz type={CAPS[slide].viz} color={CAPS[slide].color} /></div>
            <div className="lddemo-cardtxt">
              <div className="lddemo-cardn">{String(slide + 1).padStart(2, "0")} / 10</div>
              <h3>{CAPS[slide].icon} {CAPS[slide].title}</h3>
              <p>{CAPS[slide].blurb}</p>
            </div>
          </div>
          <div className="lddemo-dots">
            {CAPS.map((_, i) => (
              <button key={i} className={i === slide ? "on" : ""} onClick={() => setSlide(i)} aria-label={`Go to capability ${i + 1}`} />
            ))}
          </div>
        </div>

        {/* ── Phase 2b · correct-once-it-learns ── */}
        <CorrectDemo />
      </div>
    </section>
  );
}

const LDDEMO_CSS = `
/* analytics-dashboard demo */
.ldd-dash{margin-top:26px;background:linear-gradient(180deg,#15171d,#0f1115);border:1px solid var(--line,rgba(255,255,255,.10));border-radius:14px;padding:18px;}
.ldd-dhead{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:16px;}
.ldd-dtitle{font-family:'Fraunces',Georgia,serif;font-size:20px;}
.ldd-dsub{color:var(--ink3,#8a93a6);font-size:12.5px;margin-top:2px;}
.ldd-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px;}
.ldd-kpi{border:1px solid rgba(255,255,255,.10);border-left:3px solid var(--c);border-radius:11px;padding:10px 12px;background:linear-gradient(180deg,rgba(255,255,255,.03),transparent);}
.ldd-kl{display:block;font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:var(--ink3,#8a93a6);margin-bottom:5px;}
.ldd-kv{font-family:'Fraunces',Georgia,serif;font-size:21px;color:var(--c);}
.ldd-drow{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px;}
.ldd-card{border:1px solid rgba(255,255,255,.10);border-radius:12px;padding:13px;background:rgba(255,255,255,.02);}
.ldd-ctitle{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--ink3,#8a93a6);margin-bottom:11px;}
.ldd-bars{display:flex;align-items:flex-end;gap:7px;height:96px;}
.ldd-bars span{flex:1;border-radius:3px 3px 0 0;min-height:6px;}
.ldd-cat{display:flex;align-items:center;gap:9px;margin-bottom:9px;}
.ldd-catl{flex:0 0 68px;font-size:12px;color:var(--ink2,#c7cdd8);}
.ldd-catbar{flex:1;height:7px;border-radius:4px;background:rgba(255,255,255,.07);overflow:hidden;}
.ldd-catbar span{display:block;height:100%;border-radius:4px;}
.ldd-catv{flex:0 0 auto;font-size:11.5px;font-family:'IBM Plex Mono',monospace;color:var(--ink2,#c7cdd8);}
.ldd-ai{display:flex;gap:8px;align-items:flex-start;font-size:13px;color:var(--ink2,#c7cdd8);line-height:1.55;margin-bottom:12px;padding:11px 13px;border:1px solid rgba(226,188,104,.25);border-radius:11px;background:rgba(226,188,104,.06);}
.ldd-aidot{color:var(--gold2,#E2BC68);}
.ldd-mgrid{display:grid;grid-template-columns:1fr 1fr;gap:6px 18px;}
.ldd-mrow{display:flex;justify-content:space-between;font-size:12.5px;color:var(--ink2,#c7cdd8);padding:5px 0;border-bottom:1px solid rgba(255,255,255,.06);}
.ldd-mrow span:last-child{font-family:'IBM Plex Mono',monospace;color:var(--gold2,#E2BC68);}
@media(max-width:880px){.ldd-kpis{grid-template-columns:repeat(2,1fr);}.ldd-drow,.ldd-mgrid{grid-template-columns:1fr;}}
.lddemo-grid{display:grid;grid-template-columns:1.15fr .85fr;gap:22px;margin-top:26px;align-items:stretch;}
@media(max-width:880px){.lddemo-grid{grid-template-columns:1fr;}}
.lddemo-doc{background:linear-gradient(180deg,#15171d,#0f1115);border:1px solid var(--line,rgba(255,255,255,.10));border-radius:14px;padding:14px;}
.lddemo-paper{position:relative;width:100%;aspect-ratio:1/1.32;background:#f7f5ef;border-radius:8px;box-shadow:0 8px 30px rgba(0,0,0,.45);overflow:hidden;color:#2a2a2a;font-family:Georgia,"Times New Roman",serif;}
.lddemo-title{position:absolute;top:5%;right:5%;font-size:clamp(16px,3.2vw,30px);letter-spacing:3px;font-weight:700;color:#b08a2e;}
.lddemo-sub{position:absolute;font-size:9px;letter-spacing:2px;color:#9a9488;font-family:system-ui,sans-serif;}
.lddemo-rule{position:absolute;left:5%;width:90%;height:1px;background:#d8d2c4;}
.lddemo-th{position:absolute;left:5%;width:90%;display:flex;justify-content:space-between;font-size:9px;letter-spacing:1.4px;color:#9a9488;font-family:system-ui,sans-serif;text-transform:uppercase;}
.lddemo-th span:nth-child(2),.lddemo-th span:nth-child(3){width:18%;text-align:right;}
.lddemo-tr{position:absolute;left:5%;width:90%;display:flex;justify-content:space-between;font-size:clamp(9px,1.4vw,13px);color:#3a3a3a;}
.lddemo-tr span:nth-child(2),.lddemo-tr span:nth-child(3){width:18%;text-align:right;}
.lddemo-txnrow{position:absolute;left:5%;width:90%;display:grid;gap:8px;align-items:center;font-size:clamp(8px,1.3vw,11.5px);color:#3a3a3a;font-family:system-ui,sans-serif;}
.lddemo-txnrow.txn{grid-template-columns:52px 1fr auto;}
.lddemo-txnrow.items{grid-template-columns:1fr 26px auto;}
.lddemo-txnrow span:first-child{color:#8a857a;}
.lddemo-txnrow span:last-child{text-align:right;font-variant-numeric:tabular-nums;font-weight:600;}
.lddemo-txnrow span:nth-child(2){white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.lddemo-txnrow.items span:nth-child(2){text-align:center;color:#9a9488;font-weight:400;}
/* line-item extraction (side panel) */
.lddemo-lix{display:flex;flex-direction:column;gap:7px;}
.lddemo-litbl{display:flex;flex-direction:column;border:1px solid var(--line,rgba(255,255,255,.11));border-radius:9px;overflow:hidden;background:rgba(255,255,255,.02);}
.lddemo-litrow{display:grid;gap:9px;padding:6px 11px;font-size:11.5px;align-items:center;border-bottom:1px solid var(--line,rgba(255,255,255,.06));}
.lddemo-litrow:last-child{border-bottom:none;}
.lddemo-litrow.txn{grid-template-columns:44px 1fr auto;}
.lddemo-litrow.items{grid-template-columns:1fr 24px auto;}
.lddemo-litrow span:first-child{color:var(--ink3,#8a8f9c);font-variant-numeric:tabular-nums;white-space:nowrap;}
.lddemo-litrow span:nth-child(2){color:var(--ink,#eaeaea);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.lddemo-litrow.items span:nth-child(2){text-align:center;color:var(--ink3,#8a8f9c);}
.lddemo-litrow span:last-child{text-align:right;font-family:ui-monospace,monospace;color:#e6b25a;font-weight:600;}
.lddemo-box{position:absolute;border:2px solid;border-radius:4px;display:flex;align-items:center;padding:0 6px;transition:background .25s,border-color .25s,box-shadow .25s;cursor:default;}
.lddemo-box.on{box-shadow:0 0 0 3px rgba(var(--bc),.30);z-index:2;}
.lddemo-val{font-size:clamp(10px,1.5vw,14px);color:#23252b;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.lddemo-hl{position:absolute;background:rgba(245,210,70,.34);border:2px solid rgba(224,162,59,.85);border-radius:4px;display:flex;align-items:center;padding:0 6px;}
.lddemo-hl span{font-size:clamp(9px,1.4vw,13px);color:#3a3a3a;font-family:Georgia,serif;}
.lddemo-note{position:absolute;background:#1c1f27;color:#f0d99a;border:1px solid rgba(224,162,59,.5);border-radius:6px;padding:3px 7px;font-size:10px;font-family:system-ui,sans-serif;max-width:34%;box-shadow:0 4px 14px rgba(0,0,0,.4);}
.lddemo-side{display:flex;flex-direction:column;gap:12px;}
.lddemo-fhead{display:flex;justify-content:space-between;align-items:center;font-size:11px;text-transform:uppercase;letter-spacing:1px;color:var(--ink3,#8a8f9c);}
.lddemo-pill{font-family:system-ui,sans-serif;text-transform:none;letter-spacing:0;font-size:11px;padding:3px 9px;border-radius:999px;}
.lddemo-pill.ok{background:rgba(63,164,122,.16);color:#7fd6ab;border:1px solid rgba(63,164,122,.4);}
.lddemo-list{display:flex;flex-direction:column;gap:6px;}
.lddemo-row{display:grid;grid-template-columns:14px 1fr auto auto;gap:9px;align-items:center;text-align:left;padding:9px 11px;border-radius:9px;border:1px solid var(--line,rgba(255,255,255,.08));background:rgba(255,255,255,.02);color:var(--ink2,#c8ccd6);cursor:pointer;transition:background .2s,border-color .2s,transform .15s;}
.lddemo-row:hover,.lddemo-row.on{background:rgba(255,255,255,.06);border-color:rgba(200,160,76,.5);transform:translateX(2px);}
.lddemo-chip{width:11px;height:11px;border-radius:3px;}
.lddemo-flabel{font-size:11px;color:var(--ink3,#8a8f9c);text-transform:uppercase;letter-spacing:.5px;}
.lddemo-fval{font-family:ui-monospace,monospace;font-size:13px;color:var(--ink,#eef);}
.lddemo-conf{font-family:ui-monospace,monospace;font-size:10px;color:#caa14a;background:rgba(224,162,59,.14);padding:2px 6px;border-radius:5px;}
.lddemo-conf.hi{color:#7fd6ab;background:rgba(63,164,122,.16);}
.lddemo-ask{margin-top:4px;background:linear-gradient(135deg,rgba(139,127,214,.12),rgba(104,150,206,.10));border:1px solid rgba(139,127,214,.30);border-radius:11px;padding:12px 13px;display:flex;flex-direction:column;gap:7px;}
.lddemo-q{font-size:13px;color:#cfd3dd;font-style:italic;}
.lddemo-a{font-size:13px;color:var(--ink,#eef);line-height:1.5;}
.lddemo-cite{color:#9aa0ad;font-size:11px;font-style:normal;}
.lddemo-stats{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-top:26px;}
@media(max-width:880px){.lddemo-stats{grid-template-columns:repeat(2,1fr);}}
.lddemo-stat{border-radius:13px;padding:18px 16px;border:1px solid var(--line,rgba(255,255,255,.10));display:flex;flex-direction:column;gap:5px;justify-content:center;min-height:96px;}
.lddemo-stat.s1{background:linear-gradient(135deg,rgba(104,150,206,.18),rgba(104,150,206,.04));}
.lddemo-stat.s2{background:linear-gradient(135deg,rgba(63,164,122,.18),rgba(63,164,122,.04));}
.lddemo-stat.s3{background:linear-gradient(135deg,rgba(139,127,214,.18),rgba(139,127,214,.04));}
.lddemo-stat.s4{background:linear-gradient(135deg,rgba(224,162,59,.18),rgba(224,162,59,.04));}
.lddemo-stat.s5{background:linear-gradient(135deg,rgba(216,98,94,.14),rgba(63,164,122,.06));align-items:center;}
.lddemo-snum{font-size:clamp(20px,3vw,30px);font-weight:800;color:var(--ink,#fff);letter-spacing:-.5px;}
.lddemo-slab{font-size:11px;color:var(--ink3,#9aa0ad);text-transform:uppercase;letter-spacing:.6px;}
.lddemo-donut{display:flex;flex-direction:column;align-items:center;gap:6px;color:var(--ink,#eef);}
.lddemo-donut span{font-size:11px;color:var(--ink3,#9aa0ad);text-transform:uppercase;letter-spacing:.6px;}

/* ── capability carousel ── */
.lddemo-carousel{margin-top:34px;}
.lddemo-cartop{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;}
.lddemo-arrows{display:flex;gap:8px;}
.lddemo-arrows button{width:32px;height:32px;border-radius:50%;border:1px solid var(--line,rgba(255,255,255,.14));background:rgba(255,255,255,.04);color:var(--ink2,#c8ccd6);font-size:18px;line-height:1;cursor:pointer;transition:background .2s,border-color .2s;}
.lddemo-arrows button:hover{background:rgba(200,160,76,.16);border-color:rgba(200,160,76,.6);}
.lddemo-card{display:grid;grid-template-columns:200px 1fr;gap:22px;align-items:center;min-height:188px;
  border:1px solid var(--line,rgba(255,255,255,.10));border-left:4px solid var(--ac,#c8a04c);border-radius:14px;
  padding:24px 26px;background:linear-gradient(135deg,rgba(255,255,255,.05),rgba(255,255,255,.01));}
@media(max-width:680px){.lddemo-card{grid-template-columns:1fr;text-align:center;}}
.lddemo-cardviz{display:flex;align-items:center;justify-content:center;height:150px;background:#0f1115;border-radius:10px;border:1px solid var(--line,rgba(255,255,255,.08));}
.lddemo-cardn{font-family:ui-monospace,monospace;font-size:11px;letter-spacing:1px;color:var(--ac,#c8a04c);margin-bottom:6px;}
.lddemo-cardtxt h3{font-size:clamp(17px,2.4vw,23px);margin:0 0 8px;color:var(--ink,#fff);}
.lddemo-cardtxt p{font-size:14px;line-height:1.55;color:var(--ink2,#c8ccd6);margin:0;max-width:46ch;}
.lddemo-dots{display:flex;gap:7px;justify-content:center;margin-top:16px;flex-wrap:wrap;}
.lddemo-dots button{width:8px;height:8px;border-radius:50%;border:none;background:rgba(255,255,255,.18);cursor:pointer;padding:0;transition:background .2s,transform .2s;}
.lddemo-dots button.on{background:var(--gold,#c8a04c);transform:scale(1.5);}
/* mini-visuals */
.lddemo-mv{position:relative;width:160px;height:120px;}
.lddemo-mv-doc{background:#f7f5ef;border-radius:6px;box-shadow:inset 0 0 0 1px rgba(0,0,0,.08);}
.lddemo-mv-c{display:flex;align-items:center;justify-content:center;}
.lddemo-mv-chat{display:flex;flex-direction:column;gap:8px;justify-content:center;padding:0 6px;}
.lddemo-bub{font-size:12px;padding:7px 10px;border-radius:10px;max-width:90%;}
.lddemo-bub.q{align-self:flex-start;background:rgba(255,255,255,.07);color:#cfd3dd;}
.lddemo-bub.a{align-self:flex-end;background:rgba(255,255,255,.03);border:1px solid;color:var(--ink,#eef);}
.lddemo-minitbl{display:grid;grid-template-columns:repeat(3,1fr);gap:6px 14px;font-size:13px;color:#8a8f9c;}
.lddemo-minitbl span{display:flex;align-items:center;justify-content:center;}

/* ── Phase 2b · correct-once-it-learns ── */
.lddemo-cgrid{display:grid;grid-template-columns:300px 1fr;gap:26px;align-items:center;margin-top:14px;}
@media(max-width:760px){.lddemo-cgrid{grid-template-columns:1fr;}}
.lddemo-cpaper{position:relative;width:100%;aspect-ratio:1/0.72;background:#f7f5ef;border-radius:8px;box-shadow:0 8px 26px rgba(0,0,0,.4);overflow:hidden;}
.lddemo-crow{position:absolute;left:8%;width:84%;display:flex;justify-content:space-between;font-family:Georgia,serif;font-size:clamp(11px,1.7vw,15px);color:#3a3a3a;}
.lddemo-crow.tot{font-weight:800;color:#23252b;border-top:1px solid #d8d2c4;padding-top:5px;}
.lddemo-cbox{position:absolute;left:6%;width:88%;height:22%;border:2.5px solid;border-radius:5px;transition:top .7s cubic-bezier(.5,1.6,.4,1),background .5s,border-color .5s;}
.lddemo-cbadge{position:absolute;top:-10px;right:6px;font-family:ui-monospace,monospace;font-size:11px;font-weight:700;background:#0f1115;padding:1px 7px;border-radius:999px;}
.lddemo-ctxt{display:flex;flex-direction:column;}
.lddemo-csteps{display:flex;flex-direction:column;gap:7px;margin-bottom:12px;}
.lddemo-cstep{font-size:13px;color:var(--ink3,#8a8f9c);display:flex;gap:9px;align-items:center;transition:color .25s;}
.lddemo-cstep b{display:inline-grid;place-items:center;width:20px;height:20px;border-radius:50%;background:rgba(255,255,255,.06);color:var(--ink3,#8a8f9c);font-size:11px;flex:0 0 auto;transition:background .25s,color .25s;}
.lddemo-cstep.on{color:var(--ink,#fff);}
.lddemo-cstep.on b{background:var(--gold,#c8a04c);color:#1a1c22;}
.lddemo-was{color:#e6b25a;text-decoration:line-through;}
.lddemo-learned{margin-top:13px;background:linear-gradient(135deg,rgba(63,164,122,.16),rgba(63,164,122,.04));border:1px solid rgba(63,164,122,.4);border-radius:10px;padding:11px 13px;font-size:13px;color:#bfe9d3;}
.lddemo-learned span{display:block;margin-top:4px;font-size:11px;color:#8aa99a;}

/* ── document switcher ── */
.lddemo-switch{display:flex;align-items:center;gap:8px;margin-top:18px;flex-wrap:wrap;}
.lddemo-swbtn{font-family:inherit;font-size:13px;font-weight:600;color:var(--ink2,#c8ccd6);
  background:rgba(255,255,255,.03);border:1px solid var(--line,rgba(255,255,255,.10));
  border-radius:999px;padding:8px 16px;cursor:pointer;transition:background .18s,border-color .18s,color .18s;}
.lddemo-swbtn:hover{border-color:rgba(200,160,76,.5);color:var(--ink,#fff);}
.lddemo-swbtn.on{background:linear-gradient(150deg,rgba(200,160,76,.95),rgba(180,140,60,.95));color:#1a1407;border-color:transparent;}
.lddemo-swhint{font-size:11px;color:var(--ink3,#8a8f9c);font-style:italic;margin-left:2px;}

/* ── trust card ── */
.lddemo-trust{display:grid;grid-template-columns:auto 1fr;gap:16px;align-items:center;
  background:linear-gradient(135deg,rgba(63,164,122,.12),rgba(63,164,122,.03));
  border:1px solid rgba(63,164,122,.30);border-radius:13px;padding:15px 16px;}
.lddemo-treasons{display:flex;flex-direction:column;gap:7px;min-width:0;}
.lddemo-treason{display:grid;grid-template-columns:1fr 46px 30px;gap:8px;align-items:center;}
.lddemo-trl{font-size:11px;color:var(--ink2,#c8ccd6);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.lddemo-trbar{height:5px;border-radius:3px;background:rgba(255,255,255,.10);overflow:hidden;}
.lddemo-trbar span{display:block;height:100%;background:rgba(63,164,122,.9);border-radius:3px;}
.lddemo-trv{font-family:ui-monospace,monospace;font-size:10px;color:#7fd6ab;text-align:right;}
.lddemo-tnote{grid-column:1 / -1;font-size:10.5px;color:var(--ink3,#8a8f9c);line-height:1.4;margin-top:2px;}

/* ── passport scaffolding ── */
.lddemo-pptitle{position:absolute;top:4.5%;left:6%;font-size:clamp(14px,2.6vw,24px);letter-spacing:3px;
  font-weight:700;color:#2b3a63;font-family:Georgia,serif;}
.lddemo-ppcountry{position:absolute;top:14%;left:6%;font-size:9px;letter-spacing:1.5px;color:#7a7466;
  font-family:system-ui,sans-serif;text-transform:uppercase;}
.lddemo-photo{position:absolute;top:25%;left:6%;width:28%;height:34%;border-radius:4px;
  background:repeating-linear-gradient(135deg,#d9d3c4,#d9d3c4 6px,#cfc9ba 6px,#cfc9ba 12px);border:1px solid #b9b3a3;}
.lddemo-hl.mrz{background:rgba(43,58,99,.10);border-color:rgba(43,58,99,.5);}
.lddemo-hl.mrz span{font-family:ui-monospace,monospace;font-size:clamp(7px,1.2vw,11px);letter-spacing:1px;color:#2b3a63;white-space:nowrap;overflow:hidden;}

/* ── cross-doc chat example ── */
.lddemo-swbtn.chat.on{background:linear-gradient(150deg,rgba(139,127,214,.95),rgba(104,150,206,.95));color:#fff;}
.lddemo-swbtn.chat{border-color:rgba(139,127,214,.45);}
.lddemo-chatdemo{display:grid;grid-template-columns:1.5fr 1fr;gap:18px;margin-top:20px;align-items:start;}
@media(max-width:880px){.lddemo-chatdemo{grid-template-columns:1fr;}}
.lddemo-chatpanel{background:rgba(255,255,255,.03);border:1px solid var(--line,rgba(255,255,255,.1));
  border-radius:14px;overflow:hidden;display:flex;flex-direction:column;}
.lddemo-chathead{display:flex;align-items:center;gap:9px;padding:12px 15px;font-size:13px;font-weight:600;
  color:var(--ink,#fff);border-bottom:1px solid var(--line,rgba(255,255,255,.1));
  background:linear-gradient(135deg,rgba(139,127,214,.12),rgba(104,150,206,.08));}
.lddemo-chatdot{width:8px;height:8px;border-radius:50%;background:#8b7fd6;box-shadow:0 0 0 3px rgba(139,127,214,.25);}
.lddemo-chatbody{padding:15px;display:flex;flex-direction:column;gap:12px;}
.lddemo-msg{display:flex;}
.lddemo-msg.user{justify-content:flex-end;}
.lddemo-msg.ai{justify-content:flex-start;}
.lddemo-bubble{max-width:88%;padding:10px 13px;border-radius:13px;font-size:13px;line-height:1.5;}
.lddemo-msg.user .lddemo-bubble{background:linear-gradient(150deg,rgba(200,160,76,.92),rgba(180,140,60,.92));color:#1a1407;border-bottom-right-radius:4px;font-weight:600;}
.lddemo-msg.ai .lddemo-bubble{background:rgba(255,255,255,.05);border:1px solid var(--line,rgba(255,255,255,.1));color:var(--ink,#eee);border-bottom-left-radius:4px;}
.lddemo-mtable{border-collapse:collapse;margin:9px 0 4px;width:100%;font-size:12px;}
.lddemo-mtable td,.lddemo-mtable th{padding:4px 8px;border-bottom:1px solid var(--line,rgba(255,255,255,.08));text-align:left;}
.lddemo-mtable td.num{text-align:right;font-family:ui-monospace,monospace;color:#e6b25a;}
.lddemo-mtable.diff th{font-size:10px;text-transform:uppercase;letter-spacing:.04em;color:var(--ink3,#8a8f9c);}
.lddemo-mtable.diff td:first-child{color:var(--ink3,#8a8f9c);}
.lddemo-mcite{margin-top:7px;font-size:10.5px;color:#a99ce0;background:rgba(139,127,214,.12);
  border-radius:6px;padding:3px 8px;display:inline-block;}
.lddemo-chatfoot{margin-top:auto;padding:11px 15px;font-size:10.5px;color:var(--ink3,#8a8f9c);line-height:1.5;
  border-top:1px solid var(--line,rgba(255,255,255,.08));background:rgba(255,255,255,.02);}
.lddemo-chatside{display:flex;flex-direction:column;gap:10px;}
.lddemo-chatside-h{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--ink3,#8a8f9c);margin-bottom:2px;}
.lddemo-chatcap{display:flex;gap:11px;align-items:flex-start;background:rgba(255,255,255,.03);
  border:1px solid var(--line,rgba(255,255,255,.09));border-radius:11px;padding:11px 13px;}
.lddemo-chatcap>span{font-size:19px;line-height:1;}
.lddemo-chatcap b{font-size:13px;color:var(--ink,#fff);display:block;margin-bottom:2px;}
.lddemo-chatcap p{margin:0;font-size:11.5px;color:var(--ink2,#c8ccd6);line-height:1.45;}
`;
