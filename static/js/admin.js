/**
 * admin.js
 * Admin dashboard frontend. Talks only to the local /admin/api/* routes.
 */

(function () {
  const $ = (id) => document.getElementById(id);
  let activeConversationId = null;
  let currentRole = null;

  async function api(path, options = {}) {
    const res = await fetch(path, {
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (res.status === 401) {
      window.location.href = "/admin/login";
      throw new Error("Not authenticated");
    }
    return res;
  }

  // ---------------------------------------------------------------------
  // Auth gate
  // ---------------------------------------------------------------------

  async function checkSession() {
    const res = await fetch("/admin/api/session", { credentials: "include" });
    const data = await res.json();
    if (!data.authenticated) {
      window.location.href = "/admin/login";
      return null;
    }
    $("admin-username-label").textContent = data.username + " (" + data.role + ")";
    $("admin-app").classList.remove("admin-hidden");
    currentRole = data.role;

    if (data.role === "superadmin") {
      $("tab-btn-backups").classList.remove("admin-hidden");
      $("tab-btn-team").classList.remove("admin-hidden");
    }
    if (data.role === "admin" || data.role === "superadmin") {
      $("tab-btn-settings").classList.remove("admin-hidden");
    }
    if (data.role === "agent") {
      // Agents can view/reply/note but not manage the knowledge base.
      $("kb-upload-input").disabled = true;
      $("kb-upload-btn").disabled = true;
      $("kb-upload-status").textContent = "Knowledge base management requires an admin or superadmin role.";
    }

    return data;
  }

  $("admin-logout-btn").addEventListener("click", async () => {
    await api("/admin/api/logout", { method: "POST" });
    window.location.href = "/admin/login";
  });

  // ---------------------------------------------------------------------
  // Notifications
  // ---------------------------------------------------------------------

  async function loadNotifications() {
    const res = await api("/admin/api/notifications");
    const data = await res.json();
    renderNotifications(data.notifications, data.unread_count);
  }

  function renderNotifications(items, unreadCount) {
    const badge = $("admin-notif-badge");
    if (unreadCount > 0) {
      badge.textContent = unreadCount > 9 ? "9+" : unreadCount;
      badge.classList.remove("admin-hidden");
    } else {
      badge.classList.add("admin-hidden");
    }

    if (!items.length) {
      $("admin-notif-list").innerHTML = `<div class="admin-notif-item">No notifications yet.</div>`;
      return;
    }

    $("admin-notif-list").innerHTML = items
      .map(
        (n) => `
      <div class="admin-notif-item ${n.is_read ? "" : "unread"}" data-id="${n.id}">
        <div class="title">${n.title}</div>
        ${n.message ? `<div>${escapeHtml(n.message).slice(0, 100)}</div>` : ""}
        <div class="time">${formatDate(n.created_at)}</div>
      </div>`
      )
      .join("");

    document.querySelectorAll(".admin-notif-item[data-id]").forEach((item) => {
      item.addEventListener("click", async () => {
        await api(`/admin/api/notifications/${item.dataset.id}/read`, { method: "POST" });
        loadNotifications();
      });
    });
  }

  $("admin-notif-bell").addEventListener("click", (e) => {
    e.stopPropagation();
    $("admin-notif-dropdown").classList.toggle("admin-hidden");
  });

  document.addEventListener("click", (e) => {
    if (!$("admin-notif-dropdown").contains(e.target) && e.target !== $("admin-notif-bell")) {
      $("admin-notif-dropdown").classList.add("admin-hidden");
    }
  });

  $("admin-notif-mark-all").addEventListener("click", async (e) => {
    e.stopPropagation();
    await api("/admin/api/notifications/read-all", { method: "POST" });
    loadNotifications();
  });

  // ---------------------------------------------------------------------
  // Tabs
  // ---------------------------------------------------------------------

  document.querySelectorAll(".admin-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".admin-tab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".admin-tab-panel").forEach((p) => p.classList.remove("active"));
      tab.classList.add("active");
      $("tab-" + tab.dataset.tab).classList.add("active");
    });
  });

  // ---------------------------------------------------------------------
  // Stats
  // ---------------------------------------------------------------------

  async function loadStats() {
    const res = await api("/admin/api/analytics/summary");
    const s = await res.json();
    const cards = [
      { label: "Total Conversations", value: s.total_conversations },
      { label: "Active Conversations", value: s.active_conversations },
      { label: "Total Customers", value: s.total_customers },
      { label: "New Leads", value: s.new_leads },
      { label: "Returning Customers", value: s.returning_customers },
      { label: "Avg Chat Duration", value: formatDuration(s.avg_chat_duration_seconds) },
      { label: "Unanswered Questions", value: s.unanswered_questions },
    ];
    $("admin-stats-grid").innerHTML = cards
      .map((c) => `<div class="admin-stat-card"><div class="value">${c.value}</div><div class="label">${c.label}</div></div>`)
      .join("");
  }

  function formatDuration(seconds) {
    if (!seconds) return "0s";
    const m = Math.floor(seconds / 60);
    const s = Math.round(seconds % 60);
    return m > 0 ? `${m}m ${s}s` : `${s}s`;
  }

  // ---------------------------------------------------------------------
  // Conversations
  // ---------------------------------------------------------------------

  async function loadConversations() {
    const params = new URLSearchParams();
    const search = $("conv-search").value.trim();
    const status = $("conv-status-filter").value;
    const dateFrom = $("conv-date-from").value;
    const dateTo = $("conv-date-to").value;
    if (search) params.set("search", search);
    if (status) params.set("status", status);
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);

    const res = await api("/admin/api/conversations?" + params.toString());
    const data = await res.json();
    renderConversations(data.conversations);
  }

  function renderConversations(rows) {
    if (!rows.length) {
      $("conv-table-body").innerHTML = `<tr><td colspan="8" style="text-align:center;color:#7a8194;">No conversations found.</td></tr>`;
      return;
    }
    $("conv-table-body").innerHTML = rows
      .map(
        (r) => `
      <tr data-id="${r.id}">
        <td>${r.full_name || "Anonymous"}</td>
        <td>${r.email || "-"}</td>
        <td>${r.phone || "-"}</td>
        <td>${r.company_name || "-"}</td>
        <td>${r.interested_service || "-"}</td>
        <td><span class="admin-badge ${r.status}">${r.status}</span></td>
        <td>${formatDate(r.started_at)}</td>
        <td>View &rarr;</td>
      </tr>`
      )
      .join("");

    document.querySelectorAll("#conv-table-body tr").forEach((tr) => {
      tr.addEventListener("click", () => openConversationModal(tr.dataset.id));
    });
  }

  function formatDate(iso) {
    if (!iso) return "-";
    const d = new Date(iso);
    return d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  }

  $("conv-search-btn").addEventListener("click", loadConversations);
  $("conv-export-btn").addEventListener("click", () => {
    const fmt = $("conv-export-format").value;
    const params = new URLSearchParams({ format: fmt });
    const search = $("conv-search").value.trim();
    const status = $("conv-status-filter").value;
    if (search) params.set("search", search);
    if (status) params.set("status", status);
    window.location.href = "/admin/api/export/conversations?" + params.toString();
  });

  // ---------------------------------------------------------------------
  // Conversation detail modal
  // ---------------------------------------------------------------------

  async function openConversationModal(conversationId) {
    activeConversationId = conversationId;
    const res = await api(`/admin/api/conversations/${conversationId}`);
    const conv = await res.json();

    const c = conv.customer || {};
    const hasContact = Boolean(c.email || c.phone);
    const displayName = c.full_name || "Anonymous visitor";
    const initials = c.full_name
      ? c.full_name.trim().split(/\s+/).slice(0, 2).map((p) => p[0].toUpperCase()).join("")
      : "?";

    $("conv-modal-customer").innerHTML = `
      <div class="admin-client-avatar">${escapeHtml(initials)}</div>
      <div class="admin-client-details">
        <div class="admin-client-name-row">
          <span class="admin-client-name">${escapeHtml(displayName)}</span>
          <span class="admin-client-tag ${hasContact ? "" : "new"}">${hasContact ? "Contact on file" : "No contact shared"}</span>
        </div>
        <div class="admin-client-meta">
          ${c.email ? `📧 <a href="mailto:${encodeURI(c.email)}">${escapeHtml(c.email)}</a><br/>` : ""}
          ${c.phone ? `📞 <a href="tel:${encodeURI(c.phone)}">${escapeHtml(c.phone)}</a><br/>` : ""}
          ${c.company_name ? `🏢 <strong>${escapeHtml(c.company_name)}</strong><br/>` : ""}
          🕒 Started ${formatDate(conv.started_at)}${conv.ended_at ? " &middot; Ended " + formatDate(conv.ended_at) : ""}
        </div>
      </div>
    `;

    $("conv-modal-status").value = conv.status;

    $("conv-modal-messages").innerHTML = conv.messages
      .map((m) => `<div class="admin-msg ${m.sender}">${escapeHtml(m.message)}</div>`)
      .join("");

    $("conv-modal-notes-list").innerHTML = (conv.notes || [])
      .map((n) => `<div class="admin-note">${escapeHtml(n.note)} <em>&mdash; ${n.admin_username || "admin"}</em></div>`)
      .join("") || `<div style="font-size:12px;color:#7a8194;">No notes yet.</div>`;

    $("conv-modal").classList.remove("admin-hidden");
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  $("conv-modal-close").addEventListener("click", () => {
    $("conv-modal").classList.add("admin-hidden");
    loadConversations();
  });

  $("conv-modal-status").addEventListener("change", async (e) => {
    await api(`/admin/api/conversations/${activeConversationId}/status`, {
      method: "POST",
      body: JSON.stringify({ status: e.target.value }),
    });
    loadStats();
  });

  $("conv-modal-reply-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("conv-modal-reply-input");
    const text = input.value.trim();
    if (!text) return;
    await api(`/admin/api/conversations/${activeConversationId}/reply`, {
      method: "POST",
      body: JSON.stringify({ message: text }),
    });
    input.value = "";
    openConversationModal(activeConversationId);
  });

  $("conv-modal-note-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("conv-modal-note-input");
    const text = input.value.trim();
    if (!text) return;
    await api(`/admin/api/conversations/${activeConversationId}/notes`, {
      method: "POST",
      body: JSON.stringify({ note: text }),
    });
    input.value = "";
    openConversationModal(activeConversationId);
  });

  $("conv-modal-delete").addEventListener("click", async () => {
    if (!confirm("Permanently delete this conversation and its messages?")) return;
    await api(`/admin/api/conversations/${activeConversationId}`, { method: "DELETE" });
    $("conv-modal").classList.add("admin-hidden");
    loadConversations();
    loadStats();
  });

  // ---------------------------------------------------------------------
  // Leads
  // ---------------------------------------------------------------------

  async function loadLeads() {
    const params = new URLSearchParams();
    const search = $("lead-search").value.trim();
    const status = $("lead-status-filter").value;
    if (search) params.set("search", search);
    if (status) params.set("status", status);

    const res = await api("/admin/api/leads?" + params.toString());
    const data = await res.json();
    renderLeads(data.leads);
  }

  function renderLeads(rows) {
    if (!rows.length) {
      $("lead-table-body").innerHTML = `<tr><td colspan="8" style="text-align:center;color:#7a8194;">No leads found.</td></tr>`;
      return;
    }
    $("lead-table-body").innerHTML = rows
      .map(
        (r) => `
      <tr>
        <td>${r.name || "-"}</td>
        <td>${r.email || "-"}</td>
        <td>${r.phone || "-"}</td>
        <td>${r.interested_service || "-"}</td>
        <td>${r.budget || "-"}</td>
        <td>${r.timeline || "-"}</td>
        <td>
          <select class="lead-status-select" data-id="${r.id}">
            ${["new", "contacted", "proposal_sent", "won", "lost"]
              .map((s) => `<option value="${s}" ${s === r.status ? "selected" : ""}>${s}</option>`)
              .join("")}
          </select>
        </td>
        <td>${formatDate(r.created_at)}</td>
      </tr>`
      )
      .join("");

    document.querySelectorAll(".lead-status-select").forEach((sel) => {
      sel.addEventListener("change", async () => {
        await api(`/admin/api/leads/${sel.dataset.id}/status`, {
          method: "POST",
          body: JSON.stringify({ status: sel.value }),
        });
        loadStats();
      });
    });
  }

  $("lead-search-btn").addEventListener("click", loadLeads);
  $("lead-export-btn").addEventListener("click", () => {
    const fmt = $("lead-export-format").value;
    const params = new URLSearchParams({ format: fmt });
    const status = $("lead-status-filter").value;
    if (status) params.set("status", status);
    window.location.href = "/admin/api/export/leads?" + params.toString();
  });

  // ---------------------------------------------------------------------
  // Appointments
  // ---------------------------------------------------------------------

  async function loadAppointments() {
    const status = $("appt-status-filter").value;
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    const res = await api("/admin/api/appointments?" + params.toString());
    const data = await res.json();
    renderAppointments(data.appointments);
  }

  function renderAppointments(rows) {
    if (!rows.length) {
      $("appt-table-body").innerHTML = `<tr><td colspan="7" style="text-align:center;color:#7a8194;">No appointment requests yet.</td></tr>`;
      return;
    }
    $("appt-table-body").innerHTML = rows
      .map(
        (r) => `
      <tr>
        <td>${r.full_name || "Anonymous"}</td>
        <td>${r.email || "-"}</td>
        <td>${r.phone || "-"}</td>
        <td>${r.scheduled_for || "Not specified"}</td>
        <td title="${escapeHtml(r.notes || "")}">${(r.notes || "").slice(0, 40)}</td>
        <td>
          <select class="appt-status-select" data-id="${r.id}">
            ${["requested", "confirmed", "completed", "cancelled"]
              .map((s) => `<option value="${s}" ${s === r.status ? "selected" : ""}>${s}</option>`)
              .join("")}
          </select>
        </td>
        <td>${formatDate(r.created_at)}</td>
      </tr>`
      )
      .join("");

    document.querySelectorAll(".appt-status-select").forEach((sel) => {
      sel.addEventListener("change", async () => {
        await api(`/admin/api/appointments/${sel.dataset.id}/status`, {
          method: "POST",
          body: JSON.stringify({ status: sel.value }),
        });
      });
    });
  }

  $("appt-search-btn").addEventListener("click", loadAppointments);

  // ---------------------------------------------------------------------
  // Backups
  // ---------------------------------------------------------------------

  async function loadBackups() {
    const res = await api("/admin/api/backups");
    const data = await res.json();
    renderBackups(data.backups);
  }

  function renderBackups(rows) {
    if (!rows.length) {
      $("backup-table-body").innerHTML = `<tr><td colspan="4" style="text-align:center;color:#7a8194;">No backups yet.</td></tr>`;
      return;
    }
    $("backup-table-body").innerHTML = rows
      .map(
        (b) => `
      <tr>
        <td>${b.filename}</td>
        <td>${formatDate(b.created_at)}</td>
        <td>${(b.size_bytes / 1024).toFixed(1)} KB</td>
        <td><button class="backup-restore-btn" data-filename="${b.filename}">Restore</button></td>
      </tr>`
      )
      .join("");

    document.querySelectorAll(".backup-restore-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm(`Restore the database from "${btn.dataset.filename}"? This will replace current live data (a safety backup is taken first).`)) return;
        const res = await api("/admin/api/backups/restore", {
          method: "POST",
          body: JSON.stringify({ filename: btn.dataset.filename }),
        });
        if (res.ok) {
          alert("Restored. Reloading dashboard...");
          window.location.reload();
        } else {
          const data = await res.json();
          alert("Restore failed: " + (data.error || "unknown error"));
        }
      });
    });
  }

  $("backup-run-btn").addEventListener("click", async () => {
    const res = await api("/admin/api/backups/run", { method: "POST" });
    if (res.ok) {
      loadBackups();
    } else {
      const data = await res.json();
      alert(data.error || "Backup failed (superadmin role required).");
    }
  });

  // ---------------------------------------------------------------------
  // Documents (knowledge base + customer uploads)
  // ---------------------------------------------------------------------

  async function loadKnowledgeDocuments() {
    const res = await api("/admin/api/knowledge/documents");
    const data = await res.json();
    renderKnowledgeDocuments(data.documents);
  }

  function renderKnowledgeDocuments(rows) {
    if (!rows.length) {
      $("kb-table-body").innerHTML = `<tr><td colspan="5" style="text-align:center;color:#7a8194;">No knowledge base documents yet.</td></tr>`;
      return;
    }
    $("kb-table-body").innerHTML = rows
      .map(
        (d) => `
      <tr>
        <td>${d.filename}</td>
        <td><span class="admin-badge ${d.extraction_status === "extracted" ? "resolved" : "escalated"}">${d.extraction_status}</span></td>
        <td>${((d.size_bytes || 0) / 1024).toFixed(1)} KB</td>
        <td>${formatDate(d.uploaded_at)}</td>
        <td>${currentRole === "agent" ? "" : `<button class="kb-delete-btn" data-id="${d.id}">Delete</button>`}</td>
      </tr>`
      )
      .join("");

    document.querySelectorAll(".kb-delete-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm("Remove this document from the knowledge base?")) return;
        await api(`/admin/api/knowledge/documents/${btn.dataset.id}`, { method: "DELETE" });
        loadKnowledgeDocuments();
      });
    });
  }

  $("kb-upload-btn").addEventListener("click", async () => {
    const input = $("kb-upload-input");
    if (!input.files.length) return;
    const formData = new FormData();
    formData.append("file", input.files[0]);
    $("kb-upload-status").textContent = "Uploading...";
    try {
      const res = await fetch("/admin/api/knowledge/documents", {
        method: "POST",
        credentials: "include",
        body: formData,
      });
      const data = await res.json();
      if (!res.ok) {
        $("kb-upload-status").textContent = data.error || "Upload failed.";
        return;
      }
      $("kb-upload-status").textContent = "Uploaded.";
      input.value = "";
      loadKnowledgeDocuments();
    } catch (err) {
      $("kb-upload-status").textContent = "Upload failed.";
    }
  });

  async function loadCustomerFiles() {
    const res = await api("/admin/api/files");
    const data = await res.json();
    renderCustomerFiles(data.files);
  }

  function renderCustomerFiles(rows) {
    if (!rows.length) {
      $("customer-files-table-body").innerHTML = `<tr><td colspan="4" style="text-align:center;color:#7a8194;">No customer uploads yet.</td></tr>`;
      return;
    }
    $("customer-files-table-body").innerHTML = rows
      .map(
        (f) => `
      <tr>
        <td>${f.full_name || f.email || "Anonymous"}</td>
        <td>${f.filename}</td>
        <td>${f.extraction_status || "-"}</td>
        <td>${formatDate(f.uploaded_at)}</td>
      </tr>`
      )
      .join("");
  }

  // ---------------------------------------------------------------------
  // Team (admin user management - superadmin only)
  // ---------------------------------------------------------------------

  async function loadTeam() {
    const res = await api("/admin/api/admin-users");
    if (!res.ok) return;
    const data = await res.json();
    renderTeam(data.admin_users);
  }

  function renderTeam(rows) {
    $("team-table-body").innerHTML = rows
      .map(
        (u) => `
      <tr>
        <td>${u.username}</td>
        <td>${u.role}</td>
        <td>${formatDate(u.created_at)}</td>
        <td><button class="team-delete-btn" data-id="${u.id}" data-username="${u.username}">Remove</button></td>
      </tr>`
      )
      .join("");

    document.querySelectorAll(".team-delete-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm(`Remove admin account "${btn.dataset.username}"?`)) return;
        const res = await api(`/admin/api/admin-users/${btn.dataset.id}`, { method: "DELETE" });
        const data = await res.json();
        if (!res.ok) {
          alert(data.error || "Could not remove that account.");
          return;
        }
        loadTeam();
      });
    });
  }

  $("team-create-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = $("team-username").value.trim();
    const password = $("team-password").value;
    const role = $("team-role").value;
    const statusEl = $("team-create-status");

    const res = await api("/admin/api/admin-users", {
      method: "POST",
      body: JSON.stringify({ username, password, role }),
    });
    const data = await res.json();
    if (!res.ok) {
      statusEl.textContent = data.error || "Could not create account.";
      return;
    }
    statusEl.textContent = `Created "${data.username}" as ${data.role}.`;
    $("team-create-form").reset();
    loadTeam();
  });

  // ---------------------------------------------------------------------
  // Settings (widget color theme)
  // ---------------------------------------------------------------------

  async function loadThemeSettings() {
    const res = await api("/admin/api/settings/theme");
    if (!res.ok) return;
    const data = await res.json();
    renderThemeSwatches(data.options, data.current, data.default);
  }

  function renderThemeSwatches(options, current, defaultTheme) {
    $("theme-swatch-grid").innerHTML = options
      .map(
        (opt) => `
      <button class="admin-theme-swatch ${opt.id === current ? "selected" : ""}" data-theme-id="${opt.id}" type="button">
        <span class="admin-theme-swatch-colors">
          <span style="background:${opt.swatch[0]}"></span>
          <span style="background:${opt.swatch[1]}"></span>
        </span>
        <span class="admin-theme-swatch-label">${opt.label}${opt.id === defaultTheme ? " (default)" : ""}</span>
        ${opt.id === current ? '<span class="admin-theme-swatch-check">✓ Active</span>' : ""}
      </button>`
      )
      .join("");

    document.querySelectorAll(".admin-theme-swatch").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const statusEl = $("theme-status");
        statusEl.textContent = "Saving...";
        const res = await api("/admin/api/settings/theme", {
          method: "POST",
          body: JSON.stringify({ theme: btn.dataset.themeId }),
        });
        const data = await res.json();
        if (!res.ok) {
          statusEl.textContent = data.error || "Could not save theme.";
          return;
        }
        statusEl.textContent = "Saved — the widget now uses this theme.";
        loadThemeSettings();
      });
    });
  }

  $("theme-reset-btn").addEventListener("click", async () => {
    const statusEl = $("theme-status");
    statusEl.textContent = "Restoring default...";
    const res = await api("/admin/api/settings/theme/reset", { method: "POST" });
    if (!res.ok) {
      statusEl.textContent = "Could not reset theme.";
      return;
    }
    statusEl.textContent = "Restored the default theme.";
    loadThemeSettings();
  });

  // ---------------------------------------------------------------------
  // Init
  // ---------------------------------------------------------------------

  (async function init() {
    const session = await checkSession();
    if (!session) return;
    loadStats();
    loadConversations();
    loadLeads();
    loadAppointments();
    loadKnowledgeDocuments();
    loadCustomerFiles();
    loadNotifications();
    if (session.role === "superadmin") {
      loadBackups();
      loadTeam();
    }
    if (session.role === "admin" || session.role === "superadmin") {
      loadThemeSettings();
    }
    setInterval(loadNotifications, 30000);
  })();
})();
