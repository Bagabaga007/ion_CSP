#!/bin/bash
# 测试运行脚本

set -e

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Ion CSP 测试运行脚本${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# 解析参数
TEST_LEVEL=${1:-all}

case $TEST_LEVEL in
  unit)
    echo -e "${GREEN}运行单元测试...${NC}"
    pytest tests/unit/ -v
    ;;
  integration)
    echo -e "${GREEN}运行集成测试...${NC}"
    pytest tests/integration/ -v
    ;;
  config)
    echo -e "${GREEN}运行配置测试...${NC}"
    pytest tests/config/ -v
    ;;
  system)
    echo -e "${GREEN}运行系统测试...${NC}"
    pytest tests/system/ -v
    ;;
  fast)
    echo -e "${GREEN}运行快速测试（单元+配置）...${NC}"
    pytest tests/unit/ tests/config/ -v
    ;;
  all)
    echo -e "${GREEN}运行所有测试...${NC}"
    pytest tests/ -v --cov=src/ion_CSP --cov-report=term-missing
    ;;
  coverage)
    echo -e "${GREEN}运行测试并生成覆盖率报告...${NC}"
    pytest tests/ --cov=src/ion_CSP --cov-report=html --cov-report=term
    echo -e "${YELLOW}覆盖率报告已生成到 htmlcov/index.html${NC}"
    ;;
  *)
    echo -e "${YELLOW}用法: $0 [unit|integration|config|system|fast|all|coverage]${NC}"
    echo ""
    echo "  unit        - 运行单元测试"
    echo "  integration - 运行集成测试"
    echo "  config      - 运行配置测试"
    echo "  system      - 运行系统测试"
    echo "  fast        - 运行快速测试（单元+配置）"
    echo "  all         - 运行所有测试（默认）"
    echo "  coverage    - 运行测试并生成HTML覆盖率报告"
    exit 1
    ;;
esac

echo ""
echo -e "${GREEN}✓ 测试完成！${NC}"
