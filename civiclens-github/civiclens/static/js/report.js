// CivicLens - Page 1 "Report an issue"
(() => {
  const { el, api, checkPhotos, showError } = CL;
  const $ = (id) => document.getElementById(id);

  const form = $("report-form");
  const complaint = $("complaint");
  const photoInput = $("photo-input");
  const dropzone = $("dropzone");
  const errorBox = $("form-error");
  const submitBtn = $("submit-btn");

  let photos = []; // File objects

  if (CL.getDraft()) $("pending-banner").hidden = false;

  // ---------- character counter ----------
  complaint.addEventListener("input", () => {
    $("char-count").textContent = complaint.value.length;
    if (complaint.value.trim()) showError(errorBox, "");
  });

  // ---------- photos ----------
  function addPhotos(files) {
    const { accepted, error } = checkPhotos([...files], photos.length);
    photos.push(...accepted);
    showError(errorBox, error);
    renderPreviews();
  }

  function renderPreviews() {
    $("photo-previews").replaceChildren(
      ...photos.map((file) =>
        el("div", { class: "thumb" },
          el("img", { src: URL.createObjectURL(file), alt: file.name }),
          el("button", {
            type: "button", class: "thumb-remove", "aria-label": `Remove ${file.name}`, text: "×",
            onclick: () => { photos = photos.filter((f) => f !== file); renderPreviews(); },
          })))
    );
    dropzone.hidden = photos.length >= CL.MAX_PHOTOS;
  }

  dropzone.addEventListener("click", (e) => {
    if (e.target !== photoInput) photoInput.click();
  });
  dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      photoInput.click();
    }
  });
  photoInput.addEventListener("change", () => {
    addPhotos(photoInput.files);
    photoInput.value = "";
  });

  ["dragenter", "dragover"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.add("dragover"); }));
  ["dragleave", "drop"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.remove("dragover"); }));
  dropzone.addEventListener("drop", (e) => addPhotos(e.dataTransfer.files));

  // ---------- submit ----------
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = complaint.value.trim();

    // Rule: at least text or photo
    if (!text && photos.length === 0) {
      showError(errorBox, "Please describe the issue or add a photo. At least one is required.");
      complaint.focus();
      return;
    }

    const data = new FormData();
    data.append("complaint", text);
    data.append("location", $("location").value.trim());
    photos.forEach((file) => data.append("photos", file));

    submitBtn.disabled = true;
    $("report-section").hidden = true;
    $("loading-section").hidden = false;

    try {
      const res = await api("/api/complaints", { method: "POST", body: data });
      CL.setDraft(res.ticket.ticket_id, res.edit_token);
      CL.expandPanelNextTime();
      // Page 2: open the tab of the department the ticket was routed to
      window.location.href = `/departments?dept=${encodeURIComponent(res.ticket.category)}`;
    } catch (err) {
      $("loading-section").hidden = true;
      $("report-section").hidden = false;
      showError(errorBox, err.message);
      submitBtn.disabled = false;
    }
  });
})();
