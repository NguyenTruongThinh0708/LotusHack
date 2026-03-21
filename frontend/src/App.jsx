import { useEffect, useMemo, useRef, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";
const OSRM_BASE = import.meta.env.VITE_OSRM_BASE || "https://router.project-osrm.org";
const OSRM_CACHE_TTL_MS = Number(import.meta.env.VITE_OSRM_CACHE_TTL_MS || 10 * 60 * 1000);
const OSRM_MIN_REQUEST_GAP_MS = Number(import.meta.env.VITE_OSRM_MIN_REQUEST_GAP_MS || 1500);
const OSRM_COOLDOWN_ON_LIMIT_MS = Number(import.meta.env.VITE_OSRM_COOLDOWN_ON_LIMIT_MS || 30000);

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

function flattenAdditionalTags(additionalInfo) {
  if (!additionalInfo || typeof additionalInfo !== "object") return [];
  const tags = [];
  const services = Array.isArray(additionalInfo.services) ? additionalInfo.services : [];
  const types = Array.isArray(additionalInfo.type) ? additionalInfo.type : [];
  for (const value of [...services, ...types]) {
    if (typeof value === "string" && value.trim()) tags.push(value.trim());
  }

  const extensions = Array.isArray(additionalInfo.extensions) ? additionalInfo.extensions : [];
  for (const ext of extensions) {
    if (!ext || typeof ext !== "object") continue;
    for (const key of Object.keys(ext)) {
      const values = Array.isArray(ext[key]) ? ext[key] : [];
      for (const val of values) {
        if (typeof val === "string" && val.trim()) tags.push(val.trim());
      }
    }
  }

  return tags;
}

function shortenTag(tag) {
  if (tag.length <= 20) return tag;
  return `${tag.slice(0, 19).trim()}…`;
}

function buildShopTags(shop) {
  const collected = flattenAdditionalTags(shop?.additional_info).map(shortenTag);
  if (shop?.metrics?.is_franchise) collected.push("Franchise");
  if (shop?.metrics?.multi_service) collected.push("Đa dịch vụ");

  const unique = [...new Set(collected.filter(Boolean))].slice(0, 3);
  if (unique.length === 0) return ["Tag", "Tag", "Tag"];
  if (unique.length === 1) return [...unique, "Tag"];
  return unique;
}

function parseTimeTokenToMinutes(token) {
  if (typeof token !== "string") return null;
  const m = token.trim().match(/^(\d{1,2})(?::(\d{2}))?\s*(AM|PM)$/i);
  if (!m) return null;
  let hour = Number(m[1]);
  const minute = Number(m[2] || 0);
  const meridiem = String(m[3]).toUpperCase();
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return null;
  if (hour < 1 || hour > 12 || minute < 0 || minute > 59) return null;
  if (meridiem === "AM") {
    if (hour === 12) hour = 0;
  } else if (hour !== 12) {
    hour += 12;
  }
  return hour * 60 + minute;
}

function parseWorkingWindow(rangeText) {
  if (typeof rangeText !== "string") return null;
  const text = rangeText.trim();
  if (!text) return null;

  const lower = text.toLowerCase();
  if (lower.includes("open 24 hours")) return { allDay: true };
  if (lower.includes("closed")) return { closed: true };

  const normalized = text.replace(/â€“/g, "-").replace(/[–—−]/g, "-");
  const matches = [...normalized.matchAll(/(\d{1,2}(?::\d{2})?\s*(?:AM|PM))/gi)];
  if (matches.length < 2) return null;

  const openMin = parseTimeTokenToMinutes(matches[0][1]);
  const closeMin = parseTimeTokenToMinutes(matches[1][1]);
  if (openMin == null || closeMin == null) return null;

  return { openMin, closeMin };
}

function isShopClosedNow(shop, now = new Date()) {
  if (shop?.metrics?.is_closed) return true;
  const hours = shop?.working_hours;
  if (!hours || typeof hours !== "object") return false;

  const day = now.toLocaleDateString("en-US", { weekday: "long" });
  const rawRange = hours[day];
  const parsed = parseWorkingWindow(rawRange);
  if (!parsed) return false;
  if (parsed.allDay) return false;
  if (parsed.closed) return true;

  const currentMin = now.getHours() * 60 + now.getMinutes();
  const openMin = parsed.openMin;
  const closeMin = parsed.closeMin;

  if (openMin === closeMin) return false;
  if (openMin < closeMin) {
    return !(currentMin >= openMin && currentMin < closeMin);
  }
  return !(currentMin >= openMin || currentMin < closeMin);
}

function parseBusynessTimeToMinutes(timeLabel) {
  if (typeof timeLabel !== "string") return null;
  const value = parseTimeTokenToMinutes(timeLabel.trim().toUpperCase());
  return Number.isFinite(value) ? value : null;
}

function getTodayName(now = new Date()) {
  return now.toLocaleDateString("en-US", { weekday: "long" });
}

function getTodayBusynessSeries(shop, now = new Date()) {
  const all = Array.isArray(shop?.busyness) ? shop.busyness : [];
  const dayName = getTodayName(now);
  const rows = all
    .filter((item) => item?.day === dayName)
    .map((item) => ({
      time: item?.time || "",
      percent: Math.max(0, Math.min(100, Number(item?.percent) || 0)),
      minute: parseBusynessTimeToMinutes(item?.time || "")
    }))
    .filter((item) => Number.isFinite(item.minute));
  return rows;
}

function compactSeries(series, maxPoints = 12) {
  if (series.length <= maxPoints) return series;
  const output = [];
  const step = (series.length - 1) / (maxPoints - 1);
  const used = new Set();
  for (let i = 0; i < maxPoints; i += 1) {
    const idx = Math.round(i * step);
    if (!used.has(idx)) {
      output.push(series[idx]);
      used.add(idx);
    }
  }
  return output;
}

function findClosestBusynessIndex(series, now = new Date()) {
  if (!series.length) return -1;
  const current = now.getHours() * 60 + now.getMinutes();
  let bestIdx = 0;
  let bestDiff = Number.POSITIVE_INFINITY;
  for (let i = 0; i < series.length; i += 1) {
    const linear = Math.abs(series[i].minute - current);
    const diff = Math.min(linear, 1440 - linear);
    if (diff < bestDiff) {
      bestDiff = diff;
      bestIdx = i;
    }
  }
  return bestIdx;
}

function shopDistanceKey(shop) {
  const lat = Number(shop?.latitude);
  const lng = Number(shop?.longitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  return `${lat.toFixed(6)},${lng.toFixed(6)}`;
}

function formatMeters(meters, approximate = false) {
  if (!Number.isFinite(meters) || meters <= 0) return "-- km";
  if (meters < 1000) return `${Math.max(1, Math.round(meters))} m`;
  const km = meters / 1000;
  return `${approximate ? "~" : ""}${km.toFixed(1)} km`;
}

function formatDistanceKm(shop, userCoords, routeMeters) {
  if (Number.isFinite(routeMeters)) {
    return formatMeters(routeMeters, false);
  }

  const lat = Number(shop?.latitude);
  const lng = Number(shop?.longitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return "N/A";
  if (!userCoords) return "Đang lấy vị trí";

  const km = haversineKm(userCoords.lat, userCoords.lng, lat, lng);
  if (!Number.isFinite(km)) return "-- km";
  if (km > 150) return "-- km";
  const approximate = (userCoords.accuracy ?? 0) > 1000;
  return formatMeters(km * 1000, approximate);
}

function buildMapsDirectionUrl(shop, userCoords) {
  const lat = Number(shop?.latitude);
  const lng = Number(shop?.longitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  const base = "https://www.google.com/maps/dir/?api=1";
  const destination = `destination=${encodeURIComponent(`${lat},${lng}`)}`;
  if (userCoords && Number.isFinite(userCoords.lat) && Number.isFinite(userCoords.lng)) {
    const origin = `origin=${encodeURIComponent(`${userCoords.lat},${userCoords.lng}`)}`;
    return `${base}&${origin}&${destination}&travelmode=driving`;
  }
  return `${base}&${destination}&travelmode=driving`;
}

function BusynessMiniChart({ shop, now }) {
  const today = getTodayBusynessSeries(shop, now);
  const series = compactSeries(today, 12);
  const currentIdx = findClosestBusynessIndex(series, now);
  const currentPoint = currentIdx >= 0 ? series[currentIdx] : null;

  if (!series.length) {
    return (
      <div className="busy-wrap">
        <div className="busy-header">
          <span>Mức độ đông hôm nay</span>
          <strong>--</strong>
        </div>
        <p className="busy-empty">Chưa có dữ liệu busyness cho hôm nay.</p>
      </div>
    );
  }

  return (
    <div className="busy-wrap">
      <div className="busy-header">
        <span>Mức độ đông hôm nay</span>
        <strong>{currentPoint ? `${currentPoint.percent}%` : "--"}</strong>
      </div>
      <div className="busy-chart" role="img" aria-label="Biểu đồ mức độ đông trong ngày">
        {series.map((point, idx) => {
          const h = 8 + Math.round((point.percent / 100) * 28);
          return (
            <span
              key={`${point.time}-${idx}`}
              className={`busy-bar ${idx === currentIdx ? "current" : ""}`}
              style={{ height: `${h}px` }}
              title={`${point.time} • ${point.percent}%`}
            />
          );
        })}
      </div>
      <div className="busy-scale">
        <span>{series[0]?.time || ""}</span>
        <span>{series[series.length - 1]?.time || ""}</span>
      </div>
    </div>
  );
}

function SuggestionCard({ shop, userCoords, routeMeters, now, onSelect }) {
  const tags = buildShopTags(shop);
  const closedNow = isShopClosedNow(shop, now);
  const image = getShopImage(shop);

  return (
    <article className="suggest-row" onClick={onSelect}>
      <div className="suggest-left">
        <div className="suggest-thumb">
          {image ? <img src={image} alt={shop?.name || "shop"} loading="lazy" /> : <span>No Image</span>}
        </div>
        <div className="suggest-content">
          <h4>{shop?.name || "N/A"}</h4>
          <div className="suggest-meta">
            <small className="distance-text">{formatDistanceKm(shop, userCoords, routeMeters)}</small>
            {closedNow && <span className="closed-pill">Đã đóng cửa</span>}
          </div>
          <div className="tag-frame">
            <div className="tag-row">
              {tags.map((tag, idx) => (
                <span key={`${tag}-${idx}`} className="tag-chip">
                  #{tag}
                </span>
              ))}
            </div>
          </div>
          <BusynessMiniChart shop={shop} now={now} />
        </div>
      </div>
      <span className="suggest-arrow">›</span>
    </article>
  );
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
  const [clockTick, setClockTick] = useState(Date.now());
  const [routeDistanceByShop, setRouteDistanceByShop] = useState({});

  const recorderRef = useRef(null);
  const streamRef = useRef(null);
  const chunksRef = useRef([]);
  const osrmCacheRef = useRef(new Map());
  const osrmAbortRef = useRef(null);
  const osrmDebounceRef = useRef(null);
  const osrmLastRequestAtRef = useRef(0);
  const osrmCooldownUntilRef = useRef(0);

  useEffect(() => {
    const applyMode = () => {
      setUiMode(window.innerWidth <= 900 ? "phone" : "car");
    };
    applyMode();
    window.addEventListener("resize", applyMode);
    return () => window.removeEventListener("resize", applyMode);
  }, []);

  useEffect(() => {
    const timer = setInterval(() => {
      setClockTick(Date.now());
    }, 60_000);
    return () => clearInterval(timer);
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
      () => setUserCoords(null),
      {
        enableHighAccuracy: true,
        timeout: 7000,
        maximumAge: 60_000
      }
    );
  }, []);

  useEffect(() => {
    if (!navigator.geolocation) return undefined;
    const watchId = navigator.geolocation.watchPosition(
      (pos) => {
        const next = {
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          accuracy: pos.coords.accuracy
        };
        if ((next.accuracy ?? 9999) <= 1500) {
          setUserCoords((prev) => {
            if (!prev) return next;
            const movedKm = haversineKm(prev.lat, prev.lng, next.lat, next.lng);
            const accuracyImproved = (prev.accuracy ?? 9999) - (next.accuracy ?? 9999) > 120;
            if (movedKm < 0.06 && !accuracyImproved) return prev;
            return next;
          });
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
      if (e.key === "Escape") setSelectedShop(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const now = useMemo(() => new Date(clockTick), [clockTick]);
  const canSend = useMemo(() => input.trim().length > 0 && !loading, [input, loading]);

  const topShops = useMemo(() => {
    return [...shops]
      .sort((a, b) => Number(b?._trust ?? 0) - Number(a?._trust ?? 0))
      .slice(0, 4);
  }, [shops]);

  useEffect(() => {
    return () => {
      if (osrmDebounceRef.current) {
        clearTimeout(osrmDebounceRef.current);
        osrmDebounceRef.current = null;
      }
      if (osrmAbortRef.current) {
        osrmAbortRef.current.abort();
        osrmAbortRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    if (!userCoords || topShops.length === 0) {
      setRouteDistanceByShop({});
      return;
    }

    if (osrmDebounceRef.current) {
      clearTimeout(osrmDebounceRef.current);
      osrmDebounceRef.current = null;
    }
    if (osrmAbortRef.current) {
      osrmAbortRef.current.abort();
      osrmAbortRef.current = null;
    }

    const nowTs = Date.now();
    const originKey = `${userCoords.lat.toFixed(3)},${userCoords.lng.toFixed(3)}`;
    const cachedDistances = {};
    const unresolved = [];

    for (const shop of topShops) {
      const key = shopDistanceKey(shop);
      const cacheKey = `${originKey}|${key}`;
      const lat = Number(shop?.latitude);
      const lng = Number(shop?.longitude);
      if (!key || !Number.isFinite(lat) || !Number.isFinite(lng)) continue;

      const cached = osrmCacheRef.current.get(cacheKey);
      if (cached && cached.expiresAt > nowTs && Number.isFinite(cached.meters)) {
        cachedDistances[key] = cached.meters;
      } else {
        unresolved.push({ key, cacheKey, lat, lng });
      }
    }

    setRouteDistanceByShop(cachedDistances);
    if (!unresolved.length) return;
    if (nowTs < osrmCooldownUntilRef.current) return;

    const waitForGap = Math.max(
      0,
      osrmLastRequestAtRef.current + OSRM_MIN_REQUEST_GAP_MS - nowTs
    );
    if (osrmDebounceRef.current) clearTimeout(osrmDebounceRef.current);

    osrmDebounceRef.current = setTimeout(async () => {
      if (Date.now() < osrmCooldownUntilRef.current) return;

      if (osrmAbortRef.current) {
        osrmAbortRef.current.abort();
      }

      const controller = new AbortController();
      osrmAbortRef.current = controller;

      try {
        const source = `${userCoords.lng},${userCoords.lat}`;
        const destinationString = unresolved.map((item) => `${item.lng},${item.lat}`).join(";");
        const coordinates = `${source};${destinationString}`;
        const endpoint = `${OSRM_BASE}/table/v1/driving/${coordinates}?sources=0&annotations=distance`;

        osrmLastRequestAtRef.current = Date.now();
        const resp = await fetch(endpoint, { signal: controller.signal });
        if (resp.status === 429) {
          osrmCooldownUntilRef.current = Date.now() + OSRM_COOLDOWN_ON_LIMIT_MS;
          return;
        }
        if (!resp.ok) {
          throw new Error(`OSRM request failed (${resp.status})`);
        }

        const data = await resp.json();
        const row = Array.isArray(data?.distances?.[0]) ? data.distances[0] : null;
        if (!row) return;

        const merged = {};
        const expiresAt = Date.now() + OSRM_CACHE_TTL_MS;
        for (let i = 0; i < unresolved.length; i += 1) {
          const meters = Number(row[i + 1]);
          if (Number.isFinite(meters) && meters > 0) {
            merged[unresolved[i].key] = meters;
            osrmCacheRef.current.set(unresolved[i].cacheKey, { meters, expiresAt });
          } else {
            osrmCacheRef.current.set(unresolved[i].cacheKey, {
              meters: Number.NaN,
              expiresAt: Date.now() + 60_000
            });
          }
        }

        if (Object.keys(merged).length) {
          setRouteDistanceByShop((prev) => ({ ...prev, ...merged }));
        }
      } catch (err) {
        if (err?.name === "AbortError") return;
        osrmCooldownUntilRef.current = Date.now() + 5000;
      } finally {
        if (osrmAbortRef.current === controller) {
          osrmAbortRef.current = null;
        }
      }
    }, 350 + waitForGap);
  }, [topShops, userCoords]);

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
      if (!resp.ok) throw new Error(data.detail || "Chat API failed");

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
      if (!resp.ok) throw new Error(data.detail || "STT failed");

      const transcript = (data.text || "").trim();
      setSttEngine(data.engine || "Unknown");
      if (transcript) await sendMessage(transcript);
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
      if (canSend) sendMessage(input);
    }
  }

  const mapsUrl = selectedShop ? buildMapsDirectionUrl(selectedShop, userCoords) : null;
  const selectedRouteMeters = selectedShop
    ? routeDistanceByShop[shopDistanceKey(selectedShop)]
    : undefined;

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
                    {topShops.map((shop, idx) => (
                      <SuggestionCard
                        key={`${shop?.name || "shop"}-${idx}`}
                        shop={shop}
                        userCoords={userCoords}
                        routeMeters={routeDistanceByShop[shopDistanceKey(shop)]}
                        now={now}
                        onSelect={() => setSelectedShop(shop)}
                      />
                    ))}
                  </div>
                </section>
              )}
            </section>

            {uiMode === "car" && (
              <aside className="car-suggestions">
                <h2>Top 4 Shop Suggestions</h2>
                <div className="car-shop-list">
                  {topShops.length === 0 && <p className="muted">Chưa có dữ liệu.</p>}
                  {topShops.map((shop, idx) => (
                    <SuggestionCard
                      key={`${shop?.name || "shop"}-${idx}`}
                      shop={shop}
                      userCoords={userCoords}
                      routeMeters={routeDistanceByShop[shopDistanceKey(shop)]}
                      now={now}
                      onSelect={() => setSelectedShop(shop)}
                    />
                  ))}
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
              <h3>{selectedShop?.name || "N/A"}</h3>
              <p>Tap outside to close</p>
            </div>
            <div className="shop-modal-top">
              <div className="shop-modal-image">
                {getShopImage(selectedShop) ? (
                  <img src={getShopImage(selectedShop)} alt={selectedShop?.name || "shop"} />
                ) : (
                  <span>No Image</span>
                )}
              </div>
              <div className="shop-modal-metrics">
                <div>
                  <span>Khoảng cách</span>
                  <strong>{formatDistanceKm(selectedShop, userCoords, selectedRouteMeters)}</strong>
                </div>
                <div>
                  <span>Trạng thái</span>
                  <strong>{isShopClosedNow(selectedShop, now) ? "Đã đóng cửa" : "Đang mở cửa"}</strong>
                </div>
                <BusynessMiniChart shop={selectedShop} now={now} />
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
                <strong>Phone:</strong> {selectedShop?.phone || "N/A"}
              </p>
              <p>
                <strong>Website:</strong>{" "}
                {selectedShop?.website ? (
                  <a href={selectedShop.website} target="_blank" rel="noreferrer">
                    {selectedShop.website}
                  </a>
                ) : (
                  "N/A"
                )}
              </p>
              <p>
                <strong>Dịch vụ:</strong>{" "}
                {Array.isArray(selectedShop?.additional_info?.services) &&
                selectedShop.additional_info.services.length > 0
                  ? selectedShop.additional_info.services.slice(0, 5).join(", ")
                  : "N/A"}
              </p>
              <p>
                <strong>Google Maps:</strong>{" "}
                {mapsUrl ? (
                  <a href={mapsUrl} target="_blank" rel="noreferrer" className="map-link">
                    Chỉ đường từ vị trí hiện tại
                  </a>
                ) : (
                  "Không có tọa độ cửa hàng"
                )}
              </p>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

export default App;
