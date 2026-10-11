"use strict";

// Only fills the existing form. POST /runs stays an explicit user action.
function createPromptLibrary({document, fetchLibrary, applyRequest, showRecords}) {
  const get = id => document.getElementById(id);
  const companySelect = get("library-company");
  const caseSelect = get("library-case");
  const load = get("load-library-case");
  const records = get("library-records");
  let library = {companies: []};
  function option(value, text) {
    const node = document.createElement("option");
    node.value = value;
    node.textContent = text;
    return node;
  }
  function selectedCase() {
    return library.companies.find(c => c.id === companySelect.value)?.cases.find(c => c.id === caseSelect.value);
  }
  function preview() {
    const selected = selectedCase();
    load.disabled = records.disabled = !selected;
    get("library-preview").textContent = selected
      ? `${selected.awareness === "unbranded" ? "无品牌问题" : "指定品牌问题"}${selected.variant_of ? ` · 与 ${selected.variant_of} 同义` : ""}：${selected.request.prompt}`
      : "选择题目只预览；点击载入后会替换下方问题、评价维度和实验组。";
  }
  companySelect.onchange = () => {
    const company = library.companies.find(c => c.id === companySelect.value);
    caseSelect.replaceChildren(option("", company ? "请选择一个问题" : "请先选择公司"));
    for (const c of company?.cases || []) caseSelect.append(option(c.id, `${c.id} · ${c.title}`));
    caseSelect.value = "";
    caseSelect.disabled = !company;
    preview();
  };
  caseSelect.onchange = preview;
  load.onclick = () => {
    const selected = selectedCase();
    if (selected) applyRequest(JSON.parse(JSON.stringify(selected.request)));
  };
  records.onclick = () => {
    const selected = selectedCase();
    if (selected) showRecords(selected.request.experiment_id);
  };
  async function init() {
    try {
      library = await fetchLibrary();
      companySelect.replaceChildren(option("", "请选择公司"));
      for (const company of library.companies) companySelect.append(option(company.id, `${company.name} · ${company.cases.length} 题`));
      companySelect.disabled = false;
      const count = library.companies.reduce((sum, company) => sum + company.cases.length, 0);
      get("library-status").textContent = `${library.companies.length} 家公司，共 ${count} 个问题。可修改后运行，也可对同题重复生成。人工拟题，非客户原话。`;
    } catch {
      get("library-status").textContent = "题库暂不可用，仍可在下方自由输入研究问题。";
      companySelect.disabled = caseSelect.disabled = load.disabled = records.disabled = true;
    }
  }
  return {init};
}

if (typeof module !== "undefined") module.exports = {createPromptLibrary};
