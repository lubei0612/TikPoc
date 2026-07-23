(() => {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  const state = params.get("state");
  const error = params.get("error");
  const errorDescription = params.get("error_description");

  const title = document.getElementById("result-title");
  const detail = document.getElementById("result-detail");
  const icon = document.getElementById("result-icon");
  if (!title || !detail || !icon) return;

  if (error) {
    icon.textContent = "!";
    icon.classList.add("error");
    title.textContent = "Connection was not completed";
    detail.textContent = errorDescription || "TikTok returned an authorization error. Return to the product and try connecting again.";
  } else if (code) {
    icon.textContent = "✓";
    title.textContent = "TikTok authorization received";
    detail.textContent = state
      ? "The authorization response and state were received. Return to the product to finish the secure server-side connection."
      : "The authorization response was received. Return to the product to finish the secure server-side connection.";
  } else {
    icon.textContent = "?";
    icon.classList.add("error");
    title.textContent = "No authorization response found";
    detail.textContent = "Start the TikTok connection from IKUN Product Publisher, then return here through TikTok.";
  }

  if (window.location.search) {
    window.history.replaceState({}, document.title, window.location.pathname + window.location.hash);
  }
})();
