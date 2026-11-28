"use strict";

const app = window.Telegram?.WebApp;
const token = new URLSearchParams(location.search).get("t");
const form = document.querySelector("form");
const language = document.querySelector("#language");
const fields = document.querySelector("#fields");
const status = document.querySelector("#status");
const back = document.querySelector("#back");
const submit = document.querySelector("#submit");
const validToken = /^[A-Za-z0-9_-]{8,32}$/.test(token ?? "");
const tr = (ar, en) => language.value === "ar" ? ar : en;

const info = [
  ["full_name", "الاسم الكامل", "Full name", 120],
  ["job_title", "المسمّى المهني", "Job title", 120],
  ["location", "الموقع", "Location", 120],
  ["phone", "الهاتف مع رمز الدولة", "Phone with country code", 40, "tel"],
  ["email", "البريد الإلكتروني", "Email", 254, "email"],
];
const experience = [
  ["job_title", "المسمّى", "Job title", 100],
  ["company", "الشركة", "Company", 100],
  ["start_date", "البداية", "Start", 40],
  ["end_date", "النهاية", "End", 40],
  ["responsibilities", "المسؤوليات (سطر لكل واحدة)", "Responsibilities (one per line)", 2009, "textarea", true],
];
const education = [
  ["degree", "المؤهل", "Degree", 120],
  ["institution", "المؤسسة", "Institution", 120],
  ["year_or_status", "السنة أو الحالة", "Year or status", 60, "text", true],
];
const levels = {
  ar: ["اللغة الأم", "ممتاز", "جيد جداً", "جيد", "متوسط", "مبتدئ"],
  en: ["Native", "Excellent", "Very Good", "Good", "Intermediate", "Beginner"],
};
const level = ["level", "المستوى", "Level", 30, "select"];

function element(tag, text) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  return node;
}

function field(parent, [key, ar, en, max, type = "text", optional = false]) {
  const label = element("label");
  const name = element("span");
  name.dataset.ar = ar;
  name.dataset.en = en;
  const input = element(type === "textarea" || type === "select" ? type : "input");
  input.name = key;
  input.required = !optional;
  if (type === "select") {
    levels.en.forEach((value, i) => {
      const option = element("option");
      option.value = String(i);
      input.append(option);
    });
  } else {
    input.maxLength = max;
    if (type !== "textarea") input.type = type;
    input.dir = type === "email" || type === "tel" ? "ltr" : "auto";
  }
  label.append(name, input);
  parent.append(label);
}

function button(parent, ar, en, action) {
  const node = element("button");
  node.type = "button";
  node.dataset.ar = ar;
  node.dataset.en = en;
  node.addEventListener("click", action);
  parent.append(node);
  return node;
}

function group(key, ar, en, specs, max = 1) {
  const container = element("fieldset");
  container.dataset.group = key;
  const legend = element("legend");
  legend.dataset.ar = ar;
  legend.dataset.en = en;
  container.append(legend);
  const entries = element("div");
  container.append(entries);
  if (max === 1) {
    specs.forEach(spec => field(entries, spec));
  } else {
    const add = button(container, "إضافة", "Add", () => {
      const row = element("fieldset");
      specs.forEach(spec => field(row, spec));
      button(row, "حذف", "Remove", () => {
        row.remove();
        add.disabled = false;
      });
      entries.append(row);
      add.disabled = entries.childElementCount >= max;
      localize();
    });
  }
  fields.append(container);
}

function localize() {
  document.documentElement.lang = language.value;
  document.documentElement.dir = tr("rtl", "ltr");
  document.querySelector("h1").textContent = tr("السيرة الذاتية", "Résumé");
  document.querySelector("#language-label").textContent = tr("لغة السيرة", "Résumé language");
  back.textContent = tr("رجوع", "Back");
  submit.textContent = tr("إرسال النموذج", "Submit form");
  submit.disabled = !validToken || !app?.sendData;
  document.querySelectorAll("[data-ar]").forEach(node => {
    node.textContent = node.dataset[language.value];
  });
  document.querySelectorAll("[name=level] option").forEach(option => {
    option.textContent = levels[language.value][Number(option.value)];
  });
  status.textContent = submit.disabled ? tr("افتح النموذج من بطاقة الطلب في تيليغرام.", "Open the form from the Telegram order card.") : "";
}

function values(parent) {
  return Object.fromEntries(Array.from(parent.querySelectorAll("input,textarea,select"), input => [
    input.name, input.name === "level" ? input.selectedOptions[0].textContent : input.value.trim(),
  ]));
}

const list = (value) => value.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
const section = (name) => fields.querySelector(`[data-group="${name}"] > div`);
const rows = (name) => Array.from(section(name).children, values);

group("personal_info", "المعلومات الشخصية", "Personal information", info);
group("summary", "الملخص والمهارات", "Summary and skills", [
  ["professional_summary", "الملخص المهني", "Professional summary", 800, "textarea", true],
  ["skills", "المهارات (سطر لكل مهارة)", "Skills (one per line)", 1529, "textarea", true],
]);
group("work_experience", "الخبرة", "Work experience", experience, 6);
group("education", "التعليم", "Education", education, 5);
group("languages", "اللغات", "Languages", [["language", "اللغة", "Language", 50], level], 6);

const close = () => app?.close();
back.addEventListener("click", close);
app?.BackButton?.onClick(close);
app?.BackButton?.show();
app?.ready();
language.addEventListener("change", localize);
localize();

form.addEventListener("submit", event => {
  event.preventDefault();
  if (submit.disabled) return;
  const summary = values(section("summary"));
  const work = rows("work_experience").map(job => ({ ...job, responsibilities: list(job.responsibilities) }));
  const skills = list(summary.skills);
  if (work.some(job => job.responsibilities.length > 10 || job.responsibilities.some(line => line.length > 200)) ||
      skills.length > 30 || skills.some(line => line.length > 50)) {
    status.textContent = tr("اختصر قائمة المهارات أو المسؤوليات.", "Shorten the skills or responsibilities list.");
    return;
  }
  const payload = JSON.stringify({ token, resume_language: language.value, cvData: {
    personal_info: values(section("personal_info")),
    professional_summary: summary.professional_summary.replace(/\s*\r?\n\s*/g, " "),
    work_experience: work,
    education: rows("education"),
    skills,
    languages: rows("languages"),
  } });
  if (new TextEncoder().encode(payload).length > 4096) {
    status.textContent = tr("النص طويل. اختصر البيانات قبل إرسالها.", "Shorten the form before submitting.");
    return;
  }
  submit.disabled = true;
  try {
    app.sendData(payload);
  } catch {
    status.textContent = tr("تعذّر الإرسال. أعد المحاولة.", "Submission failed. Try again.");
    submit.disabled = false;
  }
});
