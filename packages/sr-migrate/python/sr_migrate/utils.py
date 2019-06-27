# -*- mode: python; python-indent: 4 -*-
import re

def parse_table(table, headers, detailed_headers=None):
    detailed_row_pattern = re.compile(r'^  (.*?):(.*)$')
    table_rows = table.splitlines()
    first_header_row = table_rows[0]
    table_rows = iter(table_rows)

    def get_table_rows():
        row = next(table_rows)
        if row and row.startswith('--'):
            row = next(table_rows)
        while row:
            yield row
            row = next(table_rows)

    def process_headers():
        def pop_header_words(row_words, column_words):
            while (row_words and column_words and
                   column_words[0] == row_words[0]):
                column_words.pop(0)
                yield row_words.pop(0)

        def get_header_rows():
            while sum(len(words) for words in column_headers):
                try:
                    yield next(table_rows)
                except StopIteration:
                    raise KeyError('Error parsing table header')

        column_headers = [header.split() for header in headers]

        return [[[word for word in pop_header_words(row_words, column_words)]
                 for column_words in column_headers]
                for row_words in (table_row.split() for table_row in
                                  get_header_rows())]

    def get_column_specs(columns):
        prev_pos = 0
        pos = 0
        for column in (iter(column) for column in columns):
            pos = first_header_row.find(next(column), pos)
            if pos:
                yield (prev_pos, pos)
            prev_pos = pos
            for word in column:
                pos = first_header_row.find(word, pos) + len(word)
        yield (prev_pos, len(first_header_row))

    column_specs = [spec for spec in get_column_specs(process_headers()[0])]

    result = []
    for table_row in get_table_rows():
        if table_row.startswith('  ') and detailed_headers:
            match = detailed_row_pattern.match(table_row)
            if match:
                header = match.group(1).strip()
                if header in detailed_headers:
                    result[len(result)-1][header] = match.group(2).strip()
                continue

        result.append(dict(zip(headers, list(
            table_row[column_spec[0]:column_spec[1]].strip()
            for column_spec in column_specs))))

    return result
