#!/bin/bash
# 测试脚本

echo "=== Step 1: plan-backlinks ==="
cat fixtures/seed.jsonl | plan-backlinks --log-level INFO

echo ""
echo "=== Step 2: validate-backlinks ==="
cat fixtures/seed.jsonl | plan-backlinks | validate-backlinks --no-check-urls --log-level INFO

echo ""
echo "=== Step 3: publish-backlinks (dry-run) ==="
cat fixtures/seed.jsonl | plan-backlinks | validate-backlinks --no-check-urls | publish-backlinks --platform medium --mode draft --dry-run

echo ""
echo "=== 测试完成 ==="