#!/usr/bin/env python
"""Test script for CodingAgent"""

from blackfong_ecosystem.agents.coding_agent import CodingAgent

agent = CodingAgent()

# Test with Python code
python_code = '''
def calculate_sum(numbers):
    result = 0
    for i in range(len(numbers)):
        result = result + numbers[i]
    return result
'''

result = agent.process('Optimize this code', context={'code': python_code})
print('Language detected:', result['language'])
print('Task type:', result['task_type'])
print('Confidence:', f"{result['confidence']:.2f}")
print()
print(result['response'][:300] + '...')
