# spec v1

## intent
Count the number of words in a given text string and return the integer result.

## success criteria
- returns an integer
- the integer matches the actual word count of the input text
- handles empty string input by returning 0

## constraints
- do not call any external APIs
- do not write to any files
- do not access the filesystem

## escalation threshold
drift confidence floor: 0.4
timeout before CEO decides: 300 seconds

## tools allowed
- get_word_count
