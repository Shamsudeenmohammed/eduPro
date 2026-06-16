"""Sentiment analysis service — TextBlob with keyword fallback."""

import re

from .models import SentimentLabel


POSITIVE_WORDS = {
    "excellent", "great", "good", "amazing", "wonderful", "helpful", "love",
    "best", "outstanding", "impressive", "satisfied", "happy", "thank", "thanks",
    "professional", "supportive", "clear", "engaging", "organized",
}
NEGATIVE_WORDS = {
    "bad", "poor", "terrible", "awful", "hate", "worst", "slow", "rude",
    "unhelpful", "confusing", "disappointed", "frustrated", "broken", "late",
    "missing", "failed", "problem", "issue", "complaint",
}


def extract_keywords(text, limit=8):
    words = re.findall(r"[a-zA-Z]{4,}", text.lower())
    stop = {"this", "that", "with", "from", "have", "been", "were", "they", "about"}
    freq = {}
    for w in words:
        if w not in stop:
            freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])[:limit]]


def analyze_sentiment(text):
    """
    Returns (label, score, keywords).
    Uses TextBlob if available, else keyword-based scoring.
    """
    if not text or not text.strip():
        return SentimentLabel.NEUTRAL, 0.0, []

    keywords = extract_keywords(text)

    try:
        from textblob import TextBlob
        blob = TextBlob(text)
        score = blob.sentiment.polarity
    except ImportError:
        words = set(re.findall(r"[a-zA-Z]+", text.lower()))
        pos = len(words & POSITIVE_WORDS)
        neg = len(words & NEGATIVE_WORDS)
        total = pos + neg
        if total == 0:
            score = 0.0
        else:
            score = (pos - neg) / total

    if score >= 0.3:
        label = SentimentLabel.POSITIVE
    elif score <= -0.3:
        label = SentimentLabel.NEGATIVE
    elif -0.1 <= score <= 0.1:
        label = SentimentLabel.NEUTRAL
    else:
        label = SentimentLabel.MIXED

    return label, round(score, 3), keywords


def classify_feedback(feedback):
    """Apply sentiment analysis and save to Feedback instance."""
    label, score, keywords = analyze_sentiment(feedback.message)
    feedback.sentiment = label
    feedback.sentiment_score = score
    feedback.keywords = keywords
    feedback.save(update_fields=["sentiment", "sentiment_score", "keywords", "updated_at"])
    return feedback
