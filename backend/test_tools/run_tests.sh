#!/bin/bash

clear

echo -e "\e[1;34m====================================================\e[0m"
echo -e "\e[1;36m       INTELLIGENT STATE-AWARE SECURITY SYSTEM       \e[0m"
echo -e "\e[1;36m             AUTOMATED INTEGRATION TESTS             \e[0m"
echo -e "\e[1;34m====================================================\e[0m"
echo ""

# Adjusted paths: Go up two levels (out of test_tools, out of backend) to find edge_device
PYTHON_EXEC="../../edge_device/.venv/Scripts/python.exe"

if [ ! -f "$PYTHON_EXEC" ]; then
    PYTHON_EXEC="python3"
fi

FAILED_TESTS=0

# --- TEST CASE 1 ---
echo -e "\e[1;33m[RUNNING TEST 1/3] Profile: 'Quick Peek' Scenario\e[0m"
$PYTHON_EXEC ../../edge_device/main.py --test 1
if [ $? -eq 0 ]; then
    echo -e "\e[1;32m=> TEST 1: PASSED\e[0m"
else
    echo -e "\e[1;31m=> TEST 1: FAILED\e[0m"
    FAILED_TESTS=$((FAILED_TESTS + 1))
fi
echo "----------------------------------------------------"

# --- TEST CASE 2 ---
echo -e "\e[1;33m[RUNNING TEST 2/3] Profile: 'The Ghost' Scenario\e[0m"
$PYTHON_EXEC ../../edge_device/main.py --test 2
if [ $? -eq 0 ]; then
    echo -e "\e[1;32m=> TEST 2: PASSED\e[0m"
else
    echo -e "\e[1;31m=> TEST 2: FAILED\e[0m"
    FAILED_TESTS=$((FAILED_TESTS + 1))
fi
echo "----------------------------------------------------"

# --- TEST CASE 3 ---
echo -e "\e[1;33m[RUNNING TEST 3/3] Profile: 'False Positive' Scenario\e[0m"
$PYTHON_EXEC ../../edge_device/main.py --test 3
if [ $? -eq 0 ]; then
    echo -e "\e[1;32m=> TEST 3: PASSED\e[0m"
else
    echo -e "\e[1;31m=> TEST 3: FAILED\e[0m"
    FAILED_TESTS=$((FAILED_TESTS + 1))
fi
echo "===================================================="

if [ $FAILED_TESTS -eq 0 ]; then
    echo -e "\e[1;42m  ALL SYSTEM INTEGRATION TESTS PASSED SUCCESSFULLY!  \e[0m"
    exit 0
else
    echo -e "\e[1;41m  FAILURES DETECTED: $FAILED_TESTS TEST(S) DROPPED!   \e[0m"
    exit 1
fi