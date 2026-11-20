"""
Smart Engagement Engine for YouTube Videos and Completed Streams (VODs).
- Detects video type: regular video vs completed stream (was_live).
- Extracts YouTube 'Most Replayed' retention graph (heatmap) and computes priority peaks (70% weight).
- Extracts top viewer comments with timestamps and context.
- For VODs: extracts and analyzes live chat replay to identify reaction spikes (LMAO, W, POG, clip it).
"""
import re
import numpy as np

TIME_RE = re.compile(r'\b(?:(?:(\d{1,2}):)?(\d{1,2}):(\d{2}))\b')
HYPE_WORDS = {
    "lol", "lmao", "lmfao", "kekw", "haha", "hahaha", "omg", "wtf", "w", "huge w",
    "pog", "poggers", "clip", "clip it", "holy", "insane", "dead", "skull", "rip",
    "no way", "sheesh", "clutch", "grian", "epic", "cooked"
}

def parse_time_str(m: tuple) -> float:
    """Converts regex match (hours, minutes, seconds) into total seconds."""
    hr = int(m[0]) if m[0] else 0
    mn = int(m[1]) if m[1] else 0
    sc = int(m[2]) if m[2] else 0
    return float(hr * 3600 + mn * 60 + sc)

def analyze_heatmap(heatmap: list, duration: float, top_n: int = 8) -> list:
    """
    Extracts high-activity peaks from YouTube's 'Most Replayed' retention heatmap.
    Returns sorted peak intervals: [{'start': float, 'end': float, 'value': float, 'priority': float}].
    """
    if not heatmap or not isinstance(heatmap, list):
        return []
    
    # Each entry in yt-dlp heatmap has 'start_time', 'end_time', 'value' (0.0 to 1.0)
    points = []
    for p in heatmap:
        if isinstance(p, dict) and "start_time" in p and "value" in p:
            points.append({
                "start": float(p["start_time"]),
                "end": float(p.get("end_time", p["start_time"] + 5)),
                "value": float(p.get("value", 0.0))
            })
    if not points:
        return []

    # Sort by value descending and find top peaks
    values = [p["value"] for p in points]
    mean_val = float(np.mean(values))
    std_val = float(np.std(values)) or 0.1
    threshold = max(0.4, mean_val + 0.5 * std_val)

    # Filter points exceeding threshold
    peaks = [p for p in points if p["value"] >= threshold]
    if not peaks:
        # Fallback to top 15% highest points
        sorted_pts = sorted(points, key=lambda x: -x["value"])
        peaks = sorted_pts[:max(3, len(points) // 6)]

    # Cluster nearby points within 20s
    clusters = []
    for p in sorted(peaks, key=lambda x: x["start"]):
        if clusters and p["start"] - clusters[-1]["end"] <= 20:
            clusters[-1]["end"] = max(clusters[-1]["end"], p["end"])
            clusters[-1]["value"] = max(clusters[-1]["value"], p["value"])
        else:
            clusters.append(dict(p))

    # Calculate 70% priority weight
    for c in clusters:
        c["priority"] = round(c["value"] * 0.7, 3)

    clusters.sort(key=lambda x: -x["value"])
    return clusters[:top_n]


def extract_comment_highlights(comments: list, duration: float) -> list:
    """
    Finds comments containing timestamps and context (e.g. '12:45 bro did not survive').
    """
    if not comments:
        return []
    highlights = []
    for c in comments:
        text = str(c.get("text") or "").strip()
        likes = int(c.get("like_count") or 0)
        matches = TIME_RE.finditer(text)
        for m in matches:
            t_sec = parse_time_str(m.groups())
            if 0 <= t_sec <= duration:
                clean_context = text[:m.start()] + text[m.end():]
                clean_context = re.sub(r'[\r\n\t]+', ' ', clean_context).strip()
                clean_context = re.sub(r'\s+', ' ', clean_context)[:80]
                if not clean_context:
                    clean_context = "Viewer highlighted moment"
                highlights.append({
                    "start": max(0.0, t_sec - 5.0),
                    "end": min(duration, t_sec + 35.0),
                    "timestamp": t_sec,
                    "title": clean_context,
                    "likes": likes,
                    "type": "comment"
                })
    # Sort by likes
    highlights.sort(key=lambda x: -x["likes"])
    # De-duplicate timestamps within 25 seconds
    unique = []
    for h in highlights:
        if not any(abs(h["timestamp"] - u["timestamp"]) < 25 for u in unique):
            unique.append(h)
    return unique[:5]


def analyze_chat_replay(chat_messages: list, duration: float) -> list:
    """
    Analyzes live chat replay messages from completed streams (VODs).
    Computes chat velocity and detects hype spikes.
    """
    if not chat_messages:
        return []

    # Bucket messages into 10-second intervals
    bucket_sec = 10
    num_buckets = int(duration // bucket_sec) + 1
    counts = np.zeros(num_buckets, dtype=np.int32)
    hype_counts = np.zeros(num_buckets, dtype=np.int32)

    for msg in chat_messages:
        t = float(msg.get("time_in_seconds", 0.0))
        if 0 <= t < duration:
            b_idx = int(t // bucket_sec)
            counts[b_idx] += 1
            body = str(msg.get("message", "")).lower()
            if any(w in body for w in HYPE_WORDS):
                hype_counts[b_idx] += 2

    # Combined activity score
    activity = counts + hype_counts
    if len(activity) == 0 or np.max(activity) == 0:
        return []

    mean_act = float(np.mean(activity))
    std_act = float(np.std(activity)) or 1.0

    spikes = []
    # Identify buckets that exceed 1.8 standard deviations above mean
    for i, act in enumerate(activity):
        z_score = (act - mean_act) / std_act
        if z_score >= 1.8:
            t_center = i * bucket_sec + (bucket_sec / 2)
            spikes.append({
                "start": max(0.0, t_center - 15.0),
                "end": min(duration, t_center + 25.0),
                "z_score": round(float(z_score), 2),
                "messages": int(counts[i]),
                "type": "chat_spike"
            })

    # Cluster nearby chat spikes
    spikes.sort(key=lambda x: x["start"])
    clusters = []
    for s in spikes:
        if clusters and s["start"] - clusters[-1]["end"] <= 15:
            clusters[-1]["end"] = max(clusters[-1]["end"], s["end"])
            clusters[-1]["z_score"] = max(clusters[-1]["z_score"], s["z_score"])
            clusters[-1]["messages"] += s["messages"]
        else:
            clusters.append(dict(s))

    clusters.sort(key=lambda x: -x["z_score"])
    return clusters[:6]


def calculate_priority_score(base_score: float, clip_start: float, clip_end: float,
                             heatmap_peaks: list = None, comment_highlights: list = None, chat_spikes: list = None) -> tuple:
    """
    Calculates final composite clip score on a 0-100 scale incorporating:
    - 70% Most Replayed heatmap priority rule.
    - Timestamped comments bonus.
    - Chat replay reaction spikes bonus for VODs.
    Returns (final_score, reason_note).
    """
    raw_base = float(base_score or 75.0)
    # If legacy 0-10 score is passed, normalize to 0-100
    if 0.0 < raw_base <= 10.0:
        raw_base *= 10.0
    final_score = raw_base
    reasons = []

    # 1. 70% Heatmap Priority Rule
    best_hm_val = 0.0
    for hm in (heatmap_peaks or []):
        overlap = max(0.0, min(clip_end, hm["end"]) - max(clip_start, hm["start"]))
        if overlap > 5.0:
            if hm["value"] > best_hm_val:
                best_hm_val = hm["value"]

    if best_hm_val > 0.0:
        # Scale: 70% weight on heatmap retention peak (0-100 scale), 30% on base AI/audio score
        heatmap_score_100 = best_hm_val * 100.0
        final_score = (final_score * 0.3) + (heatmap_score_100 * 0.7)
        reasons.append(f"Most Replayed peak ({int(best_hm_val * 100)}% replay intensity)")

    # 2. Timestamped Comments (up to +12 bonus points on 100 scale)
    for cm in (comment_highlights or []):
        if clip_start - 5 <= cm["timestamp"] <= clip_end + 5:
            final_score = min(99.0, final_score + 12.0)
            reasons.append(f"Viewer comment: \"{cm['title'][:35]}\"")
            break

    # 3. Live Chat Reaction Spikes (up to +15 bonus points on 100 scale)
    for cs in (chat_spikes or []):
        overlap = max(0.0, min(clip_end, cs["end"]) - max(clip_start, cs["start"]))
        if overlap > 5.0:
            final_score = min(99.0, final_score + 15.0)
            reasons.append(f"Live chat hype spike ({cs['messages']} msgs, z={cs['z_score']})")
            break

    final_score = int(round(max(25.0, min(99.0, final_score))))
    note = " · ".join(reasons) if reasons else "Audio / transcript highlight"
    return final_score, note
