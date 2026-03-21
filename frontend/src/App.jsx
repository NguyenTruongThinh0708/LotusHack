import { useEffect, useMemo, useRef, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

function riskLabel(risk) {
  if (risk === "HIGH RISK") return "Nguy hiểm";
  if (risk === "CAUTION") return "Cần lưu ý";
  if (risk === "CLOSED") return "Đã đóng";
  return "An toàn";
}

function riskTone(risk) {
  if (risk === "HIGH RISK") return "danger";
  if (risk === "CAUTION") return "warning";
  if (risk === "CLOSED") return "muted";
  return "safe";
}

function haversineKm(lat1, lon1, lat2, lon2) {
  const toRad = (v) => (v * Math.PI) / 180;
  const R = 6371;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

function getShopImage(shop) {
  const reviews = Array.isArray(shop?.top_reviews) ? shop.top_reviews : [];
  for (const review of reviews) {
    const images = Array.isArray(review?.images) ? review.images : [];
    if (images.length > 0 && typeof images[0] === "string" && images[0].startsWith("http")) {
      return images[0];
    }
  }
  return null;
}

function buildShopTags(shop) {
  const tags = [];
  const services = Array.isArray(shop?.additional_info?.services) ? shop.additional_info.services : [];
  for (const service of services) {
    if (typeof service === "string" && service.trim()) {
      tags.push(service.trim());
    }
    if (tags.length >= 2) break;
  }

  if (shop?.metrics?.is_franchise) tags.push("Franchise");
  if (shop?.metrics?.multi_service) tags.push("Multi Service");
  if (shop?._risk) tags.push(riskLabel(shop._risk));

  const unique = [...new Set(tags.filter(Boolean))].slice(0, 3);
  if (unique.length === 0) return ["Tag", "Tag", "Tag"];
  if (unique.length === 1) return [...unique, "Tag"];
  return unique;
}

function formatDistanceKm(shop, userCoords) {
  const lat = Number(shop?.latitude);
  const lng = Number(shop?.longitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lng) || !userCoords) {
    return "Bật GPS";
  }
  const km = haversineKm(userCoords.lat, userCoords.lng, lat, lng);
  if (!Number.isFinite(km) || km > 120) return "-- km";
  if (km < 1) return `${Math.max(1, Math.round(km * 1000))} m`;
  if ((userCoords.accuracy ?? 0) > 1000) return `~${km.toFixed(1)} km`;
  return `${km.toFixed(1)} km`;
}

function getWorkingHoursPreview(shop) {
  const hours = shop?.working_hours;
  if (!hours || typeof hours !== "object") return "N/A";
  const pairs = Object.entries(hours).slice(0, 3);
  if (pairs.length === 0) return "N/A";
  return pairs.map(([day, time]) => `${day}: ${time}`).join(" • ");
}

function App() {
  const [uiMode, setUiMode] = useState("car");
  const [messages, setMessages] = useState([
    {
      role: "assistant",
      content: "Xin chào, mình là SafeWash AI. Bạn có thể nhập câu hỏi hoặc dùng voice để bắt đầu."
    }
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [shops, setShops] = useState([]);
  const [recording, setRecording] = useState(false);
  const [sttEngine, setSttEngine] = useState("Blaze");
  const [userCoords, setUserCoords] = useState(null);
  const [selectedShop, setSelectedShop] = useState(null);

  const recorderRef = useRef(null);
  const streamRef = useRef(null);
  const chunksRef = useRef([]);

  useEffect(() => {
    const applyMode = () => {
      setUiMode(window.innerWidth <= 900 ? "phone" : "car");
    };
    applyMode();
    window.addEventListener("resize", applyMode);
    return () => window.removeEventListener("resize", applyMode);
  }, []);

  useEffect(() => {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        if ((pos.coords.accuracy ?? 9999) <= 1500) {
          setUserCoords({
            lat: pos.coords.latitude,
            lng: pos.coords.longitude,
            accuracy: pos.coords.accuracy
          });
        }
      },
      () => {
        setUserCoords(null);
      },
      {
        enableHighAccuracy: true,
        timeout: 7000,
        maximumAge: 60_000
      }
    );
  }, []);

  useEffect(() => {
    if (!navigator.geolocation) return;
    const watchId = navigator.geolocation.watchPosition(
      (pos) => {
        const next = {
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          accuracy: pos.coords.accuracy
        };
        if ((next.accuracy ?? 9999) <= 1500) {
          setUserCoords(next);
        }
      },
      () => {},
      {
        enableHighAccuracy: true,
        timeout: 7000,
        maximumAge: 10_000
      }
    );
    return () => navigator.geolocation.clearWatch(watchId);
  }, []);

  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.key === "Escape") {
        setSelectedShop(null);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const canSend = useMemo(() => input.trim().length > 0 && !loading, [input, loading]);

  const topShops = useMemo(() => {
    return [...shops]
      .sort((a, b) => Number(b?._trust ?? 0) - Number(a?._trust ?? 0))
      .slice(0, 4);
  }, [shops]);

  async function sendMessage(text) {
    const msg = text.trim();
    if (!msg) return;
    setInput("");
    setLoading(true);
    setMessages((prev) => [...prev, { role: "user", content: msg }]);

    try {
      const resp = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: msg })
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.detail || "Chat API failed");
      }

      setMessages((prev) => [...prev, { role: "assistant", content: data.reply }]);
      setShops(Array.isArray(data.shops) ? data.shops : []);
      setSelectedShop(null);
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Unknown error";
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Không thể xử lý yêu cầu: ${detail}` }
      ]);
    } finally {
      setLoading(false);
    }
  }

  async function transcribeBlob(blob) {
    const form = new FormData();
    form.append("audio_file", blob, "recording.webm");

    try {
      const resp = await fetch(`${API_BASE}/api/voice/stt`, {
        method: "POST",
        body: form
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.detail || "STT failed");
      }

      const transcript = (data.text || "").trim();
      setSttEngine(data.engine || "Unknown");
      if (transcript) {
        await sendMessage(transcript);
      }
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Unknown error";
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Voice chưa xử lý được: ${detail}` }
      ]);
    }
  }

  async function startRecording() {
    if (!navigator.mediaDevices?.getUserMedia) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Trình duyệt này chưa hỗ trợ microphone." }
      ]);
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      streamRef.current = stream;
      recorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.onstop = async () => {
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        chunksRef.current = [];
        if (streamRef.current) {
          streamRef.current.getTracks().forEach((t) => t.stop());
          streamRef.current = null;
        }
        await transcribeBlob(blob);
      };

      recorder.start();
      setRecording(true);
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Unknown error";
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Không thể bật mic: ${detail}` }
      ]);
    }
  }

  function stopRecording() {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.stop();
      setRecording(false);
    }
  }

  function onInputKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (canSend) {
        sendMessage(input);
      }
    }
  }

  return (
    <div className={`app-shell mode-${uiMode}`}>
      <header className="app-header">
        <div>
          <h1>SafeWash Assistant</h1>
          <p>Chat by text or voice • Engine: {sttEngine}</p>
        </div>
      </header>

      <div className="device-stage">
        <div className={`device-shell ${uiMode === "phone" ? "phone-shell" : "car-shell"}`}>
          <main className="app-main">
            <section className="chat-area">
              <div className="message-list">
                {messages.map((m, idx) => (
                  <article key={`${m.role}-${idx}`} className={`message-row ${m.role}`}>
                    <div className={`message-bubble ${m.role}`}>
                      <div className="message-content">{m.content}</div>
                    </div>
                  </article>
                ))}
                {loading && (
                  <article className="message-row assistant">
                    <div className="message-bubble assistant typing">SafeWash đang phân tích...</div>
                  </article>
                )}
              </div>

              <div className="input-bar">
                <button
                  className={`mic-btn ${recording ? "recording" : ""}`}
                  onClick={recording ? stopRecording : startRecording}
                  aria-label="Voice action"
                  title="Voice action"
                >
                  {recording ? "⏹" : "🎙"}
                </button>
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={onInputKeyDown}
                  rows={1}
                  placeholder="Nhập câu hỏi của bạn..."
                />
              </div>
              <p className="input-hint">Enter để gửi • Shift + Enter để xuống dòng</p>

              {uiMode === "phone" && (
                <section className="phone-suggestions">
                  <h3>Top Shop Suggestions</h3>
                  <div className="phone-shop-scroll">
                    {topShops.length === 0 && <p className="muted">Chưa có dữ liệu.</p>}
                    {topShops.map((shop, idx) => {
                      const img = getShopImage(shop);
                      const tags = buildShopTags(shop);
                      return (
                        <article key={`${shop.name}-${idx}`} className="suggest-row" onClick={() => setSelectedShop(shop)}>
                          <div className="suggest-left">
                            <div className="suggest-thumb">
                              {img ? <img src={img} alt={shop.name || "shop"} loading="lazy" /> : <span>No Image</span>}
                            </div>
                            <div className="suggest-content">
                              <h4>{shop.name || "N/A"}</h4>
                              <div className="suggest-meta">
                                <small className="distance-text">{formatDistanceKm(shop, userCoords)}</small>
                                <span className="meta-dot">•</span>
                                <span className={`status-pill ${riskTone(shop._risk)}`}>
                                  {riskLabel(shop._risk)}
                                </span>
                              </div>
                              <div className="tag-frame">
                                <div className="tag-row">
                                  {tags.map((tag, i) => (
                                    <span key={`${tag}-${i}`} className="tag-chip">
                                      #{tag}
                                    </span>
                                  ))}
                                </div>
                              </div>
                            </div>
                          </div>
                          <span className="suggest-arrow">›</span>
                        </article>
                      );
                    })}
                  </div>
                </section>
              )}
            </section>

            {uiMode === "car" && (
              <aside className="car-suggestions">
                <h2>Top 4 Shop Suggestions</h2>
                <div className="car-shop-list">
                  {topShops.length === 0 && <p className="muted">Chưa có dữ liệu.</p>}
                  {topShops.map((shop, idx) => {
                    const img = getShopImage(shop);
                    const tags = buildShopTags(shop);
                    return (
                      <article key={`${shop.name}-${idx}`} className="suggest-row" onClick={() => setSelectedShop(shop)}>
                        <div className="suggest-left">
                          <div className="suggest-thumb">
                            {img ? <img src={img} alt={shop.name || "shop"} loading="lazy" /> : <span>No Image</span>}
                          </div>
                          <div className="suggest-content">
                            <h4>{shop.name || "N/A"}</h4>
                            <div className="suggest-meta">
                              <small className="distance-text">{formatDistanceKm(shop, userCoords)}</small>
                              <span className="meta-dot">•</span>
                              <span className={`status-pill ${riskTone(shop._risk)}`}>
                                {riskLabel(shop._risk)}
                              </span>
                            </div>
                            <div className="tag-frame">
                              <div className="tag-row">
                                {tags.map((tag, i) => (
                                  <span key={`${tag}-${i}`} className="tag-chip">
                                    #{tag}
                                  </span>
                                ))}
                              </div>
                            </div>
                          </div>
                        </div>
                        <span className="suggest-arrow">›</span>
                      </article>
                    );
                  })}
                </div>
              </aside>
            )}
          </main>
        </div>
      </div>

      {selectedShop && (
        <div className="shop-modal-backdrop" onClick={() => setSelectedShop(null)}>
          <section className="shop-modal" onClick={(e) => e.stopPropagation()}>
            <div className="shop-modal-head">
              <h3>{selectedShop.name || "N/A"}</h3>
              <p>Tap outside to close</p>
            </div>
            <div className="shop-modal-top">
              <div className="shop-modal-image">
                {getShopImage(selectedShop) ? (
                  <img src={getShopImage(selectedShop)} alt={selectedShop.name || "shop"} />
                ) : (
                  <span>No Image</span>
                )}
              </div>
              <div className="shop-modal-metrics">
                <div>
                  <span>Risk</span>
                  <strong>{riskLabel(selectedShop._risk)}</strong>
                </div>
                <div>
                  <span>Distance</span>
                  <strong>{formatDistanceKm(selectedShop, userCoords)}</strong>
                </div>
              </div>
            </div>
            <div className="shop-modal-tags">
              {buildShopTags(selectedShop).map((tag, idx) => (
                <span key={`${tag}-${idx}`} className="tag-chip">
                  {tag}
                </span>
              ))}
            </div>
            <div className="shop-modal-info">
              <p>
                <strong>Phone:</strong> {selectedShop.phone || "N/A"}
              </p>
              <p>
                <strong>Website:</strong> {selectedShop.website || "N/A"}
              </p>
              <p>
                <strong>Services:</strong>{" "}
                {Array.isArray(selectedShop?.additional_info?.services) &&
                selectedShop.additional_info.services.length > 0
                  ? selectedShop.additional_info.services.slice(0, 5).join(", ")
                  : "N/A"}
              </p>
              <p>
                <strong>Working hours:</strong> {getWorkingHoursPreview(selectedShop)}
              </p>
              <p>
                <strong>Coordinates:</strong> {selectedShop.latitude ?? "N/A"}, {selectedShop.longitude ?? "N/A"}
              </p>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

export default App;
