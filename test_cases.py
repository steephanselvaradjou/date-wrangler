from date_parser import  master_date_preprocessor
if __name__ == "__main__":
    test_data = [
        "sales for Q1 2024", "performance in 2nd qtr of 2024", "a report on qtr3 24", "show me data for 4 quarter '23",
        "first half 2024 results", "a summary of H2 2025", "from H2 2024 to H1 2024", "revenue for Jan 2024", "expenses in september of '23",
        "the entire year 2024", "a report for year 25", "What was the total for CY2024?", "Please summarize FY2024.",
        "How did we do in fiscal year 25?", "summarize financial year 2025", "1st quarter of FY24", "4th qtr FY25",
        "H1 FY2024", "H2 FY2024", "sales from 1q 2024 to 3q 2024", "Jan 24 - mar 24",
        "December 2023 to February 2024", "from H2 2023 to H1 2024", "Q4 CY23 to Q1 FY25", "Nov to Jan",
        "Q4 2024 to Q1", "from H2 to H1 2025", "sales for the last 4 quarters", "show me the past 6 months",
        "a summary of the previous 2 years", "the next 3 quarters", "performance in the following year",
        "last quarter", "previous year", "next month", "5 years after", "6 month before", "2 qtr ago", "last 5 cy",
        "past 2 fy", "next 3 FY", "last FY", "next CY", "this month", "this year", "this quarter",
        "contribution for third q of 2024", "contribution for the third quarter of 2024",
        "contribution for second month of 2024", "last qtr", "sales for the current year",
        "a report for current year 2025", "q1   Fy24   TO   h1   cY25",
        "Let's compare last 3 months with performance in Q1 FY24.", "What about the 5th quarter of 2024?",
        "H3 CY23 is not a valid period.", "Order number 2024 is pending", "A 2-day trip to see the 2024 eclipse",
        "Sales increased by 25 percent", "sales in calendar year 2024", "Fy 2024-25", "Fy 2024-2025", "FY24/25", "Fy 24-25",
        "compare q1 sales of fy 24 with q4 sales of fy 24", "compare sales of q2 fy 24 with q4 sales of fy 24",
        "date from 10.05.2024 to 15.06.2024", "data between 2024-05-10 and 2024-06-15",
        "Sales of YTD", "performance in the last YTD period", "YTD september 2024",
        "jan-mar 2024", "jan 24-mar 2025", "jan 24", "jan to mar", "feb to apr 2025",
        "period from March 2025 to March 2025"
    ]
    for ex in test_data:
        print(f'INPUT: "{ex}"')
        result = master_date_preprocessor(ex)
        print(f'FINAL OUTPUT: "{result}"\n')