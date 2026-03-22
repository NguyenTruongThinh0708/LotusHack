import { useEffect, useMemo, useRef, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";
const OSRM_BASE = import.meta.env.VITE_OSRM_BASE || "https://router.project-osrm.org";
const OSRM_CACHE_TTL_MS = Number(import.meta.env.VITE_OSRM_CACHE_TTL_MS || 10 * 60 * 1000);
const OSRM_MIN_REQUEST_GAP_MS = Number(import.meta.env.VITE_OSRM_MIN_REQUEST_GAP_MS || 1500);
const OSRM_COOLDOWN_ON_LIMIT_MS = Number(import.meta.env.VITE_OSRM_COOLDOWN_ON_LIMIT_MS || 30000);
const TTS_LANG = "vi-VN";
const TTS_RATE = 1.3;
const BUSY_TIMELINE_STEP_MIN = 120;
const BUSY_TIMELINE_SLOTS = 12;
const GEO_REQUEST_TIMEOUT_MS = Number(import.meta.env.VITE_GEO_REQUEST_TIMEOUT_MS || 12000);
const DAY_ALIAS_MAP = {
  monday: "Monday",
  mon: "Monday",
  "thu 2": "Monday",
  "thu hai": "Monday",
  tuesday: "Tuesday",
  tue: "Tuesday",
  tues: "Tuesday",
  "thu 3": "Tuesday",
  "thu ba": "Tuesday",
  wednesday: "Wednesday",
  wed: "Wednesday",
  "thu 4": "Wednesday",
  "thu tu": "Wednesday",
  thursday: "Thursday",
  thu: "Thursday",
  thur: "Thursday",
  thurs: "Thursday",
  "thu 5": "Thursday",
  "thu nam": "Thursday",
  friday: "Friday",
  fri: "Friday",
  "thu 6": "Friday",
  "thu sau": "Friday",
  saturday: "Saturday",
  sat: "Saturday",
  "thu 7": "Saturday",
  sunday: "Sunday",
  sun: "Sunday",
  "chu nhat": "Sunday",
  cn: "Sunday"
};

function normalizeKeywordText(text) {
  if (typeof text !== "string") return "";
  return text
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/đ/g, "d")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function isNearbyQueryText(text) {
  const normalized = normalizeKeywordText(text);
  if (!normalized) return false;
  const keywords = [
    "gan day",
    "gan nhat",
    "xung quanh",
    "o gan toi",
    "near me",
    "nearby",
    "nearest",
    "closest"
  ];
  return keywords.some((k) => normalized.includes(k));
}

function serializeUserCoords(coords) {
  if (!coords) return null;
  const lat = Number(coords.lat);
  const lng = Number(coords.lng);
  const accuracy = Number(coords.accuracy);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  return {
    lat,
    lng,
    accuracy: Number.isFinite(accuracy) ? accuracy : null
  };
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

const CRITERIA_TAG_LABELS = {
  clean: "Sạch sẽ",
  speed: "Nhanh",
  price: "Giá tốt",
  support: "Hỗ trợ tốt",
  safe: "An toàn"
};

function shortenTag(tag) {
  if (tag.length <= 20) return tag;
  return `${tag.slice(0, 19).trim()}…`;
}

function buildCriteriaTagsFromMetrics(shop) {
  const metrics =
    shop?.metrics && typeof shop.metrics === "object"
      ? shop.metrics
      : shop?.store_metrics && typeof shop.store_metrics === "object"
        ? shop.store_metrics
        : {};

  let hasCriteriaMetric = false;
  const tags = [];
  for (const [key, label] of Object.entries(CRITERIA_TAG_LABELS)) {
    const score = Number(metrics?.[key]);
    if (!Number.isFinite(score)) continue;
    hasCriteriaMetric = true;
    if (score >= 1) {
      tags.push(label);
    }
  }
  return { tags, hasCriteriaMetric };
}

function buildShopTags(shop) {
  const tagsFromBackend = Array.isArray(shop?.tags)
    ? shop.tags.filter((tag) => typeof tag === "string" && tag.trim()).map((tag) => tag.trim())
    : [];
  const { tags: criteriaTags, hasCriteriaMetric } = buildCriteriaTagsFromMetrics(shop);
  const finalTags = hasCriteriaMetric ? criteriaTags : tagsFromBackend;

  const unique = [...new Set(finalTags.map(shortenTag))].slice(0, 3);
  if (unique.length === 0) return ["Chưa có tag"];
  return unique;
}

function parseTimeTokenToMinutes(token) {
  if (typeof token !== "string") return null;
  const cleaned = token
    .replace(/\u202f/g, " ")
    .replace(/â€¯/g, " ")
    .replace(/\u00a0/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!cleaned) return null;

  const ampmMatch = cleaned.match(/^(\d{1,2})(?::(\d{2}))?\s*([AaPp]\.?\s*[Mm]\.?)$/);
  if (ampmMatch) {
    let hour = Number(ampmMatch[1]);
    const minute = Number(ampmMatch[2] || 0);
    const meridiem = ampmMatch[3].toUpperCase().replace(/\./g, "").replace(/\s+/g, "");
    if (!Number.isFinite(hour) || !Number.isFinite(minute)) return null;
    if (hour < 1 || hour > 12 || minute < 0 || minute > 59) return null;
    if (meridiem === "AM") {
      if (hour === 12) hour = 0;
    } else if (hour !== 12) {
      hour += 12;
    }
    return hour * 60 + minute;
  }

  const h24Match = cleaned.match(/^(\d{1,2})(?::|h)?(\d{1,2})?$/i);
  if (!h24Match) return null;
  const hour = Number(h24Match[1]);
  const minute = Number(h24Match[2] || 0);
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return null;
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
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

function formatMinuteLabel(minute) {
  if (!Number.isFinite(minute)) return "";
  const total = ((Math.round(minute) % 1440) + 1440) % 1440;
  let hour = Math.floor(total / 60);
  const minutePart = total % 60;
  const meridiem = hour >= 12 ? "PM" : "AM";
  hour %= 12;
  if (hour === 0) hour = 12;
  if (minutePart === 0) return `${hour} ${meridiem}`;
  return `${hour}:${String(minutePart).padStart(2, "0")} ${meridiem}`;
}

function getTodayName(now = new Date()) {
  return now.toLocaleDateString("en-US", { weekday: "long" });
}

function normalizeDayName(rawDay) {
  if (typeof rawDay !== "string") return "";
  const base = rawDay
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
  return DAY_ALIAS_MAP[base] || rawDay.trim();
}

function getTodayBusynessSeries(shop, now = new Date()) {
  const all = Array.isArray(shop?.busyness) ? shop.busyness : [];
  const dayName = normalizeDayName(getTodayName(now));
  const rows = all
    .filter((item) => {
      const normalizedDay = normalizeDayName(String(item?.day || ""));
      return normalizedDay && normalizedDay === dayName;
    })
    .map((item) => ({
      time: item?.time || "",
      percent: Math.max(0, Math.min(100, Number(String(item?.percent ?? "").replace("%", "")) || 0)),
      minute: parseBusynessTimeToMinutes(item?.time || "")
    }))
    .filter((item) => Number.isFinite(item.minute));
  return rows;
}

function buildFixedTimelineSlots() {
  const output = [];
  for (let i = 0; i < BUSY_TIMELINE_SLOTS; i += 1) {
    const minute = i * BUSY_TIMELINE_STEP_MIN;
    output.push({ minute, time: formatMinuteLabel(minute) });
  }
  return output;
}

function buildFixedBusynessSeries(shop, now = new Date()) {
  const todaySeries = getTodayBusynessSeries(shop, now);
  const slots = buildFixedTimelineSlots();
  if (!todaySeries.length) {
    return slots.map((slot) => ({
      minute: slot.minute,
      time: slot.time,
      percent: 0,
      hasData: false
    }));
  }

  const byMinute = new Map(todaySeries.map((item) => [item.minute, item]));
  return slots.map((slot) => {
    const exact = byMinute.get(slot.minute);
    if (exact) {
      return {
        minute: slot.minute,
        time: slot.time,
        percent: exact.percent,
        hasData: true
      };
    }

    let nearest = null;
    let bestDiff = Number.POSITIVE_INFINITY;
    for (const point of todaySeries) {
      const diff = Math.abs(point.minute - slot.minute);
      if (diff < bestDiff) {
        bestDiff = diff;
        nearest = point;
      }
    }
    if (nearest && bestDiff <= 45) {
      return {
        minute: slot.minute,
        time: slot.time,
        percent: nearest.percent,
        hasData: true
      };
    }
    return {
      minute: slot.minute,
      time: slot.time,
      percent: 0,
      hasData: false
    };
  });
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
  const backendMeters = Number(shop?._distance_m);
  if (Number.isFinite(backendMeters) && backendMeters > 0) {
    return formatMeters(backendMeters, false);
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
  const series = buildFixedBusynessSeries(shop, now);
  const hasAnyData = series.some((point) => point.hasData);
  const currentIdx = findClosestBusynessIndex(series, now);
  const currentPoint = currentIdx >= 0 && series[currentIdx]?.hasData ? series[currentIdx] : null;

  return (
    <div className="busy-wrap">
      <div className="busy-header">
        <span>Mức độ đông hôm nay</span>
        <strong>{currentPoint ? `${currentPoint.percent}%` : "--"}</strong>
      </div>
      <div className="busy-chart" role="img" aria-label="Biểu đồ mức độ đông trong ngày">
        {series.map((point, idx) => {
          const h = point.hasData ? 8 + Math.round((point.percent / 100) * 28) : 8;
          return (
            <span
              key={`${point.time}-${idx}`}
              className={`busy-bar ${point.hasData ? "" : "missing"} ${idx === currentIdx && point.hasData ? "current" : ""}`.trim()}
              style={{ height: `${h}px` }}
              title={point.hasData ? `${point.time} • ${point.percent}%` : `${point.time} • No data`}
            />
          );
        })}
      </div>
      <div className="busy-scale">
        <span>{series[0]?.time || ""}</span>
        <span>{series[Math.floor(series.length / 2)]?.time || ""}</span>
        <span>{series[series.length - 1]?.time || ""}</span>
      </div>
      {!hasAnyData && <p className="busy-empty">Chưa có dữ liệu busyness cho hôm nay.</p>}
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
      content: "Xin chào, mình là WashGo AI. Bạn có thể dùng voice để bắt đầu."
    }
  ]);
  const [loading, setLoading] = useState(false);
  const [shops, setShops] = useState([]);
  const [recording, setRecording] = useState(false);
  const [sttEngine, setSttEngine] = useState("Blaze");
  const [userCoords, setUserCoords] = useState(null);
  const [geoStatus, setGeoStatus] = useState("idle");
  const [geoError, setGeoError] = useState("");
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
  const speechUtteranceRef = useRef(null);
  const ttsAbortRef = useRef(null);
  const ttsAudioRef = useRef(null);
  const ttsAudioUrlRef = useRef("");

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

  function buildCoordsFromPosition(pos) {
    const lat = Number(pos?.coords?.latitude);
    const lng = Number(pos?.coords?.longitude);
    const accuracy = Number(pos?.coords?.accuracy);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
    return {
      lat,
      lng,
      accuracy: Number.isFinite(accuracy) ? accuracy : null
    };
  }

  function applyCoordsFromPosition(pos) {
    const next = buildCoordsFromPosition(pos);
    if (!next) return null;

    setUserCoords((prev) => {
      if (!prev) return next;
      const movedKm = haversineKm(prev.lat, prev.lng, next.lat, next.lng);
      const prevAcc = Number.isFinite(prev.accuracy) ? prev.accuracy : 999999;
      const nextAcc = Number.isFinite(next.accuracy) ? next.accuracy : 999999;
      const accuracyImproved = nextAcc + 40 < prevAcc;
      if (movedKm < 0.03 && !accuracyImproved) return prev;
      return next;
    });
    setGeoStatus("ready");
    setGeoError("");
    return next;
  }

  function handleGeoError(error) {
    const code = Number(error?.code || 0);
    if (code === 1) {
      setGeoStatus("denied");
      setGeoError("Bạn đang chặn quyền vị trí trên trình duyệt.");
      return;
    }
    if (code === 2) {
      setGeoStatus("unavailable");
      setGeoError("Thiết bị chưa cung cấp được vị trí. Hãy bật GPS/Wi-Fi.");
      return;
    }
    if (code === 3) {
      setGeoStatus("timeout");
      setGeoError("Lấy vị trí bị timeout, đang thử lại bằng nguồn gần đúng.");
      return;
    }
    setGeoStatus("error");
    setGeoError("Không lấy được vị trí hiện tại.");
  }

  function requestOneShotLocation(timeoutMs = GEO_REQUEST_TIMEOUT_MS) {
    return new Promise((resolve) => {
      if (!navigator.geolocation) {
        setGeoStatus("unsupported");
        setGeoError("Trình duyệt không hỗ trợ geolocation.");
        resolve(null);
        return;
      }

      setGeoStatus((prev) => (prev === "ready" ? prev : "requesting"));
      navigator.geolocation.getCurrentPosition(
        (pos) => resolve(applyCoordsFromPosition(pos)),
        (err) => {
          handleGeoError(err);
          resolve(null);
        },
        {
          enableHighAccuracy: false,
          timeout: Math.max(5000, timeoutMs),
          maximumAge: 120_000
        }
      );
    });
  }

  useEffect(() => {
    const host = window.location.hostname;
    const isLocalhost = host === "localhost" || host === "127.0.0.1" || host === "::1";
    if (!window.isSecureContext && !isLocalhost) {
      setGeoStatus("insecure");
      setGeoError("Geolocation chỉ hoạt động trên HTTPS hoặc localhost.");
      return;
    }
    void requestOneShotLocation();
  }, []);

  useEffect(() => {
    if (!navigator.geolocation) return undefined;
    const host = window.location.hostname;
    const isLocalhost = host === "localhost" || host === "127.0.0.1" || host === "::1";
    if (!window.isSecureContext && !isLocalhost) return undefined;

    const watchId = navigator.geolocation.watchPosition(
      (pos) => {
        applyCoordsFromPosition(pos);
      },
      (err) => {
        // Don't spam UI for transient watcher errors except permission denied.
        if (Number(err?.code || 0) === 1) {
          handleGeoError(err);
        }
      },
      {
        enableHighAccuracy: false,
        timeout: Math.max(8000, GEO_REQUEST_TIMEOUT_MS),
        maximumAge: 30_000
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
  const topShops = useMemo(() => {
    const items = Array.isArray(shops) ? [...shops] : [];
    const hasDistanceRanking = items.some((shop) => Number.isFinite(Number(shop?._distance_km)));
    if (hasDistanceRanking) {
      items.sort((a, b) => {
        const ad = Number(a?._distance_km);
        const bd = Number(b?._distance_km);
        const aMissing = !Number.isFinite(ad);
        const bMissing = !Number.isFinite(bd);
        if (aMissing !== bMissing) return aMissing ? 1 : -1;
        if (!aMissing && !bMissing && ad !== bd) return ad - bd;
        return Number(b?._trust ?? 0) - Number(a?._trust ?? 0);
      });
    }
    // Keep backend order for non-distance queries.
    return items.slice(0, 4);
  }, [shops]);

  useEffect(() => {
    return () => {
      if (window.speechSynthesis) {
        window.speechSynthesis.cancel();
      }
      if (ttsAbortRef.current) {
        ttsAbortRef.current.abort();
        ttsAbortRef.current = null;
      }
      if (ttsAudioRef.current) {
        try {
          ttsAudioRef.current.pause();
        } catch {}
        ttsAudioRef.current = null;
      }
      if (ttsAudioUrlRef.current) {
        URL.revokeObjectURL(ttsAudioUrlRef.current);
        ttsAudioUrlRef.current = "";
      }
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

  function stopTtsPlayback() {
    if (window.speechSynthesis) {
      window.speechSynthesis.cancel();
    }
    if (ttsAbortRef.current) {
      ttsAbortRef.current.abort();
      ttsAbortRef.current = null;
    }
    if (ttsAudioRef.current) {
      try {
        ttsAudioRef.current.pause();
      } catch {}
      ttsAudioRef.current = null;
    }
    if (ttsAudioUrlRef.current) {
      URL.revokeObjectURL(ttsAudioUrlRef.current);
      ttsAudioUrlRef.current = "";
    }
    speechUtteranceRef.current = null;
  }

  function pickVietnameseVoice() {
    if (!window.speechSynthesis) return null;
    const voices = window.speechSynthesis.getVoices() || [];
    if (!voices.length) return null;
    return (
      voices.find((v) => String(v.lang || "").toLowerCase() === "vi-vn") ||
      voices.find((v) => String(v.lang || "").toLowerCase().startsWith("vi")) ||
      null
    );
  }

  async function speakViaServerTts(text) {
    const content = String(text || "").trim();
    if (!content) return false;

    try {
      const controller = new AbortController();
      ttsAbortRef.current = controller;
      const resp = await fetch(`${API_BASE}/api/voice/tts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: content }),
        signal: controller.signal
      });
      if (!resp.ok) return false;

      const blob = await resp.blob();
      if (!blob || blob.size === 0) return false;
      const audioUrl = URL.createObjectURL(blob);
      ttsAudioUrlRef.current = audioUrl;

      const audio = new Audio(audioUrl);
      audio.playbackRate = TTS_RATE;
      ttsAudioRef.current = audio;
      audio.onended = () => {
        if (ttsAudioUrlRef.current) {
          URL.revokeObjectURL(ttsAudioUrlRef.current);
          ttsAudioUrlRef.current = "";
        }
        ttsAudioRef.current = null;
      };
      await audio.play();
      return true;
    } catch {
      return false;
    } finally {
      ttsAbortRef.current = null;
    }
  }

  async function speakAssistantReply(text) {
    const content = String(text || "").trim();
    if (!content) {
      return;
    }
    stopTtsPlayback();

    const canBrowserTts = window.speechSynthesis && typeof SpeechSynthesisUtterance !== "undefined";
    if (canBrowserTts) {
      const viVoice = pickVietnameseVoice();
      if (viVoice) {
        const utterance = new SpeechSynthesisUtterance(content);
        utterance.lang = TTS_LANG;
        utterance.rate = TTS_RATE;
        utterance.pitch = 1;
        utterance.volume = 1;
        utterance.voice = viVoice;
        speechUtteranceRef.current = utterance;
        window.speechSynthesis.speak(utterance);
        return;
      }
    }

    await speakViaServerTts(content);
  }

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
    setLoading(true);
    setMessages((prev) => [...prev, { role: "user", content: msg }]);

    try {
      let coordsPayload = serializeUserCoords(userCoords);
      if (!coordsPayload && isNearbyQueryText(msg)) {
        const oneshot = await requestOneShotLocation(GEO_REQUEST_TIMEOUT_MS);
        coordsPayload = serializeUserCoords(oneshot);
      }

      const resp = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: msg,
          user_coords: coordsPayload
        })
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "Chat API failed");

      const assistantReply = String(data.reply || "").trim();
      setMessages((prev) => [...prev, { role: "assistant", content: assistantReply }]);
      void speakAssistantReply(assistantReply);
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
    stopTtsPlayback();
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

  const mapsUrl = selectedShop ? buildMapsDirectionUrl(selectedShop, userCoords) : null;
  const selectedRouteMeters = selectedShop
    ? routeDistanceByShop[shopDistanceKey(selectedShop)]
    : undefined;
  const geoHintText =
    geoStatus === "ready"
      ? `Vị trí đã sẵn sàng${Number.isFinite(userCoords?.accuracy) ? ` (±${Math.round(userCoords.accuracy)}m)` : ""}.`
      : geoStatus === "requesting"
        ? "Đang lấy vị trí hiện tại..."
        : geoStatus === "denied"
          ? "Bạn chưa cấp quyền vị trí. Hãy Allow Location trong trình duyệt."
          : geoStatus === "insecure"
            ? "Trang cần chạy HTTPS hoặc localhost để lấy vị trí."
            : geoError || "Chưa có vị trí hiện tại.";

  return (
    <div className={`app-shell mode-${uiMode}`}>
      <header className="app-header">
        <div>
          <h1>WashGo Assistant</h1>
          <p>Voice-only mode • Engine: {sttEngine}</p>
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
                    <div className="message-bubble assistant typing">WashGo đang phân tích...</div>
                  </article>
                )}
              </div>

              <div className="voice-dock">
                <button
                  className={`mic-btn ${recording ? "recording" : ""}`}
                  onClick={recording ? stopRecording : startRecording}
                  aria-label="Voice action"
                  title="Voice action"
                >
                  {recording ? (
                    <span className="mic-stop-icon" aria-hidden="true" />
                  ) : (
                    <svg className="mic-icon" viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M12 15a3 3 0 0 0 3-3V7a3 3 0 1 0-6 0v5a3 3 0 0 0 3 3Z" />
                      <path d="M18 11.5a1 1 0 1 0-2 0 4 4 0 1 1-8 0 1 1 0 1 0-2 0 6 6 0 0 0 5 5.91V20H9a1 1 0 1 0 0 2h6a1 1 0 1 0 0-2h-2v-2.59A6 6 0 0 0 18 11.5Z" />
                    </svg>
                  )}
                </button>
              </div>
              <p className="voice-hint">Nhấn mic để nói với assistant.</p>
              <p className="voice-hint">{geoHintText}</p>

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
