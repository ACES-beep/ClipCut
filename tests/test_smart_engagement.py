"""
Test 1: Regular YouTube Video (Heatmap 70% Priority + Timestamped Comments).
Test 2: Completed Stream / VOD (was_live Detection + Live Chat Replay Spike Analysis + 2K Quality).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from studio import heatmap, media, render

def test_1_regular_video_heatmap_and_comments():
    print("=" * 60)
    print("RUNNING TEST 1: Regular Video Heatmap (70% Priority) & Timestamped Comments")
    print("=" * 60)

    # 1. Simulate 100-point YouTube Heatmap with a strong retention spike at t=120s
    simulated_heatmap = []
    duration = 300.0
    for i in range(100):
        t0 = i * (duration / 100)
        t1 = (i + 1) * (duration / 100)
        # Background retention around 0.2 - 0.3
        val = 0.25
        # Sharp retention spike around t=120s to t=135s (index 40-45)
        if 40 <= i <= 44:
            val = 0.95
        simulated_heatmap.append({"start_time": t0, "end_time": t1, "value": val})

    # 2. Analyze heatmap peaks
    peaks = heatmap.analyze_heatmap(simulated_heatmap, duration)
    print(f"[OK] Extracted {len(peaks)} heatmap peak(s).")
    assert len(peaks) > 0, "Failed to extract heatmap peaks"
    top_peak = peaks[0]
    print(f"[OK] Top peak detected: start={top_peak['start']:.1f}s, end={top_peak['end']:.1f}s, value={top_peak['value']:.2f}")
    assert 115.0 <= top_peak["start"] <= 125.0, f"Expected peak near 120s, got {top_peak['start']}"
    assert top_peak["value"] >= 0.9, "Expected high peak value >= 0.9"

    # 3. Test 70% Priority Score Calculation
    base_ai_score = 6.0
    # Clip matching the peak
    clip_start, clip_end = 120.0, 150.0
    final_score, reason = heatmap.calculate_priority_score(base_ai_score, clip_start, clip_end, heatmap_peaks=peaks)
    expected_score = round((base_ai_score * 0.3) + (top_peak["value"] * 10.0 * 0.7), 1)
    print(f"[OK] Priority Score: Base={base_ai_score} -> Final={final_score} (Expected ~{expected_score})")
    print(f"[OK] Reason Note: {reason}")
    assert abs(final_score - expected_score) <= 0.2, f"Priority score mismatch: {final_score} vs {expected_score}"
    assert "Most Replayed peak" in reason, "Reason note should cite Most Replayed peak"

    # 4. Test Timestamped Comment Highlights
    sample_comments = [
        {"text": "Great video as always!", "like_count": 5},
        {"text": "12:45 bro did not see that coming at all lmaooo", "like_count": 42},
        {"text": "Check out 02:00 for the best part", "like_count": 18},
    ]
    highlights = heatmap.extract_comment_highlights(sample_comments, duration=900.0)
    print(f"[OK] Extracted {len(highlights)} timestamped comment highlight(s).")
    assert len(highlights) == 2, f"Expected 2 highlights, got {len(highlights)}"
    assert highlights[0]["timestamp"] == 765.0, f"Expected 12:45 (765s), got {highlights[0]['timestamp']}"
    assert highlights[0]["likes"] == 42
    print(f"[OK] Top comment highlight: {highlights[0]['timestamp']}s | \"{highlights[0]['title']}\"")
    print(">>> TEST 1 PASSED SUCCESSFULLY! <<<\n")


def test_2_completed_stream_vod_chat_spikes_and_2k():
    print("=" * 60)
    print("RUNNING TEST 2: Completed Stream (VOD) Chat Spikes & 2K Video Quality")
    print("=" * 60)

    # 1. Simulate Completed Stream VOD live chat replay (500 messages with hype cluster at t=210s)
    chat_events = []
    duration = 600.0
    import random
    random.seed(42)

    # Normal background chat (1 message every 3-5 seconds)
    t = 0.0
    while t < duration:
        chat_events.append({"time_in_seconds": t, "message": "hello streamer"})
        t += random.uniform(2.5, 5.0)

    # Massive hype spike at t=205s - 225s (100 messages with hype words)
    for _ in range(80):
        t_spike = random.uniform(205.0, 225.0)
        word = random.choice(["LMAOOO", "HUGE W", "OMG NO WAY", "POGGERS", "CLIP THAT"])
        chat_events.append({"time_in_seconds": t_spike, "message": f"{word} {word}!"})

    # 2. Analyze Chat Replay
    spikes = heatmap.analyze_chat_replay(chat_events, duration)
    print(f"[OK] Extracted {len(spikes)} chat reaction spike(s).")
    assert len(spikes) > 0, "Expected at least 1 chat spike"
    top_spike = spikes[0]
    print(f"[OK] Top reaction spike: start={top_spike['start']:.1f}s, end={top_spike['end']:.1f}s, z-score={top_spike['z_score']}, msgs={top_spike['messages']}")
    assert 190.0 <= top_spike["start"] <= 215.0, f"Expected spike near 210s, got {top_spike['start']}"
    assert top_spike["z_score"] >= 2.0, "Expected high z-score >= 2.0"

    # 3. Test Score Boosting for Chat Spikes
    base_score = 5.0
    final_score, reason = heatmap.calculate_priority_score(base_score, 205.0, 235.0, chat_spikes=spikes)
    print(f"[OK] VOD Clip Score: Base={base_score} -> Final={final_score}")
    print(f"[OK] Reason Note: {reason}")
    assert final_score >= 7.0, f"Expected boosted score >= 7.0, got {final_score}"
    assert "Live chat hype spike" in reason

    # 4. Test 2K Video Quality Settings
    canvas_w, canvas_h = (1440, 2560) # 2K vertical resolution
    fmt_str = media._video_format(1440)
    print(f"[OK] 2K Format Download String: {fmt_str}")
    assert "1440" in fmt_str, "Format string should target 1440p"
    assert "vcodec^=avc1" not in fmt_str, "AVC restriction should be removed to allow high-bitrate VP9/AV1"

    print(">>> TEST 2 PASSED SUCCESSFULLY! <<<\n")


if __name__ == "__main__":
    test_1_regular_video_heatmap_and_comments()
    test_2_completed_stream_vod_chat_spikes_and_2k()
    print("ALL 2 TESTS COMPLETED WITH 100% SUCCESS!")
