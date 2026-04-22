function readCookie(name) {
  const value = `; ${document.cookie}`;
  const parts = value.split(`; ${name}=`);
  if (parts.length === 2) {
    return decodeURIComponent(parts.pop().split(";").shift());
  }
  return "";
}

async function rawFetch(path, options = {}) {
  const isJsonBody =
    options.body &&
    !(options.body instanceof FormData) &&
    typeof options.body !== "string" &&
    !(options.body instanceof Blob);

  const headers = new Headers(options.headers || {});
  if (isJsonBody) {
    headers.set("Content-Type", "application/json");
  }
  if (!["GET", "HEAD", "OPTIONS"].includes((options.method || "GET").toUpperCase())) {
    const csrfToken = readCookie("hm_csrf");
    if (csrfToken) {
      headers.set("X-CSRF-Token", csrfToken);
    }
  }

  const response = await fetch(path, {
    credentials: "include",
    ...options,
    headers,
    body: isJsonBody ? JSON.stringify(options.body) : options.body
  });
  return response;
}

export async function apiFetch(path, options = {}) {
  let response = await rawFetch(path, options);
  if (response.status === 401 && !options.__skipRefresh) {
    const refreshResponse = await rawFetch("/api/auth/refresh", {
      method: "POST",
      __skipRefresh: true
    });
    if (refreshResponse.ok) {
      response = await rawFetch(path, { ...options, __skipRefresh: true });
    }
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = payload.detail || payload.message || detail;
    } catch {
      detail = await response.text();
    }
    const error = new Error(detail || "Request failed");
    error.status = response.status;
    throw error;
  }
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }
  return response;
}

export const api = {
  get: (path) => apiFetch(path),
  post: (path, body, extra = {}) => apiFetch(path, { method: "POST", body, ...extra }),
  put: (path, body) => apiFetch(path, { method: "PUT", body }),
  delete: (path) => apiFetch(path, { method: "DELETE" })
};

export { readCookie };
