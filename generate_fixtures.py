"""Generate test fixtures for the backlink pipeline."""
import json

# Seed fixture
seeds = [
    {
        "target_url": "https://example.com/article/one",
        "main_domain": "https://example.com",
        "language": "en",
        "platform": "medium",
        "url_mode": "A",
        "publish_mode": "draft",
        "topic": "Web Development Trends",
        "seed_keywords": ["web", "development", "trends"],
    },
    {
        "target_url": "https://blog.example.org/posts/guide",
        "main_domain": "https://blog.example.org",
        "language": "zh-CN",
        "platform": "blogger",
        "url_mode": "C",
        "publish_mode": "publish",
        "topic": "Python最佳实践",
        "seed_keywords": ["Python", "最佳实践"],
    },
    {
        "target_url": "https://tech.ru/posts/overview",
        "main_domain": "https://tech.ru",
        "language": "ru",
        "platform": "medium",
        "url_mode": "B",
        "publish_mode": "draft",
        "topic": "Cloud Infrastructure",
        "seed_keywords": ["cloud", "infrastructure"],
    },
]

with open("fixtures/seed.jsonl", "w", encoding="utf-8") as f:
    for seed in seeds:
        f.write(json.dumps(seed, ensure_ascii=False) + "\n")

print("fixtures/seed.jsonl written")