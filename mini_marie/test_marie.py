#!/usr/bin/env python3
"""
Simple test script for MARIE agent.

This script tests basic functionality without running the full demo.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from mini_marie.marie_agent import MarieAgent


async def test_basic_query():
    """Test a simple query."""
    print("="*80)
    print("TEST 1: Basic Question")
    print("="*80)
    
    marie = MarieAgent(model_name="gpt-4o-mini")
    
    question = "How many MOPs are in the knowledge graph?"
    print(f"\nQuestion: {question}")
    print("-" * 80)
    
    try:
        answer, metadata = await marie.ask(question)
        print(f"\nAnswer:\n{answer}")
        print(f"\n📊 Usage Statistics:")
        print(f"  Tokens: {metadata['aggregated_usage']['total_tokens']}")
        print(f"  Cost: ${metadata['aggregated_usage']['total_cost_usd']:.4f}")
        print(f"  Calls: {metadata['aggregated_usage']['calls']}")
        print("\n✅ Test 1 PASSED")
        return True
    except Exception as e:
        print(f"\n❌ Test 1 FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_synthesis_lookup():
    """Test synthesis information retrieval."""
    print("\n" + "="*80)
    print("TEST 2: Synthesis Lookup")
    print("="*80)
    
    marie = MarieAgent(model_name="gpt-4o-mini")
    
    synthesis_name = "UMC-1"
    print(f"\nLooking up: {synthesis_name}")
    print("-" * 80)
    
    try:
        answer, metadata = await marie.get_synthesis_info(
            synthesis_name,
            include_recipe=True,
            include_steps=False,
            include_conditions=False,
            include_products=True
        )
        print(f"\nAnswer:\n{answer}")
        print(f"\n📊 Usage Statistics:")
        print(f"  Tokens: {metadata['aggregated_usage']['total_tokens']}")
        print("\n✅ Test 2 PASSED")
        return True
    except Exception as e:
        print(f"\n❌ Test 2 FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_mop_lookup():
    """Test MOP information retrieval."""
    print("\n" + "="*80)
    print("TEST 3: MOP Lookup")
    print("="*80)
    
    marie = MarieAgent(model_name="gpt-4o-mini")
    
    mop_name = "CIAC-105"
    print(f"\nLooking up MOP: {mop_name}")
    print("-" * 80)
    
    try:
        answer, metadata = await marie.find_mop_info(mop_name)
        print(f"\nAnswer:\n{answer}")
        print(f"\n📊 Usage Statistics:")
        print(f"  Tokens: {metadata['aggregated_usage']['total_tokens']}")
        print("\n✅ Test 3 PASSED")
        return True
    except Exception as e:
        print(f"\n❌ Test 3 FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all tests."""
    print("="*80)
    print("MARIE Agent Test Suite")
    print("="*80)
    print("\nThis will test MARIE's ability to answer questions about MOPs.")
    print("Note: First test may take 10-15 seconds while MCP server starts.\n")
    
    results = []
    
    # Run tests
    results.append(await test_basic_query())
    results.append(await test_synthesis_lookup())
    results.append(await test_mop_lookup())
    
    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    passed = sum(results)
    total = len(results)
    print(f"\nTests Passed: {passed}/{total}")
    
    if passed == total:
        print("✅ All tests PASSED!")
        return 0
    else:
        print(f"❌ {total - passed} test(s) FAILED")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

