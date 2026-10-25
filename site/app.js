// No cookie, no storage: the acceptance gate only lives in the page until it is closed.
const gate = document.getElementById("accept");
const tutorial = document.getElementById("tutorial");
const toggle = () => {
  if (gate.checked) tutorial.removeAttribute("hidden");
  else tutorial.setAttribute("hidden", "");
};
gate.addEventListener("change", toggle);
toggle(); // a browser may restore the box as checked without firing change

// A copy button on each snippet; the clipboard is only written on a click.
for (const pre of tutorial.querySelectorAll(".pane pre")) {
  const box = document.createElement("div");
  box.className = "snippet";
  pre.replaceWith(box);
  box.append(pre);
  const btn = document.createElement("button");
  btn.className = "copy";
  btn.type = "button";
  btn.textContent = "Copy";
  btn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(pre.textContent);
      btn.textContent = "Copied";
    } catch {
      btn.textContent = "Select and copy";
    }
    setTimeout(() => (btn.textContent = "Copy"), 1500);
  });
  box.append(btn);
}

const out = document.getElementById("try-out");
document.getElementById("try-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const text = document.getElementById("try-text").value.trim();
  if (!text) return;
  out.removeAttribute("hidden");
  out.textContent = "...";
  try {
    const r = await fetch("/api/v1/systemone", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        state: text,
        questions: {
          tier: { type: "choice", criteria: { SIMPLE: "", MEDIUM: "", COMPLEX: "", REASONING: "" } },
        },
      }),
    });
    const body = await r.text();
    try {
      out.textContent = JSON.stringify(JSON.parse(body), null, 2);
    } catch {
      out.textContent = `${r.status} ${body}`;
    }
    if (r.status === 429) out.textContent += "\nRate limit reached: retry in a few seconds.";
  } catch (e) {
    out.textContent = `Network error: ${e.message}`;
  }
});

fetch("/status.json")
  .then((r) => r.json())
  .then((s) => {
    for (const k of ["commit", "tantive_image", "model"]) {
      document.getElementById(`st-${k}`).textContent = s[k] || "unknown";
    }
  })
  .catch(() => {
    document.getElementById("st-model").textContent = "unavailable";
  });
