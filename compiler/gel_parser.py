import os
import re
from lark import Lark, UnexpectedInput, UnexpectedToken

HERE = os.path.dirname(os.path.abspath(__file__))
GRAMMAR_FILE = os.path.join(HERE, "snes_gel.lark")
STDLIB_DIR = os.path.join(HERE, "stdlib")

def load_parser():
    if not os.path.exists(GRAMMAR_FILE):
        raise FileNotFoundError(f"Grammar file '{GRAMMAR_FILE}' not found.")
    
    with open(GRAMMAR_FILE, "r") as f:
        grammar_content = f.read()
    
    return Lark(grammar_content, parser="lalr", start="start", propagate_positions=True)


def preprocess_gel_code(code: str, base_dir: str = ".", include_dirs=()) -> str:
    # 1. Normalize line endings
    code = code.replace("\r\n", "\n").replace("\r", "\n")

    # 2. Resolve #include "file.gel" recursively
    include_regex = re.compile(r'^\s*#include\s+["<]([^">]+)[">]', re.MULTILINE)
    
    def resolve_includes(source: str, current_dir: str) -> str:
        def replace_include(match):
            filename = match.group(1)
            searched = [current_dir, *include_dirs, STDLIB_DIR]
            filepath = next((os.path.join(d, filename) for d in searched
                             if os.path.exists(os.path.join(d, filename))), None)
            if filepath is None:
                raise FileNotFoundError(
                    f"[Preprocessor Error] Included file not found: '{filename}' (searched: {', '.join(searched)})")
            with open(filepath, "r") as inc_file:
                included_content = inc_file.read()
            return resolve_includes(included_content, os.path.dirname(filepath) or current_dir)
        
        return include_regex.sub(replace_include, source)

    code = resolve_includes(code, base_dir)

    # 3. Strip single-line comments (//)
    code = re.sub(r'//.*', '', code)

    # 4. Extract #define NAME VALUE macros
    defines = {}
    define_regex = re.compile(r'^\s*#define\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.+)$', re.MULTILINE)
    
    def collect_defines(match):
        name, val = match.group(1), match.group(2).strip()
        defines[name] = val
        return ""

    code = define_regex.sub(collect_defines, code)

    # 5. Substitute defined macros throughout the body text (identifiers only)
    for name in sorted(defines.keys(), key=len, reverse=True):
        val = defines[name]
        # Match exact identifier boundaries so values aren't recursively re-substituted
        code = re.sub(r'(?<![A-Za-z0-9_])' + re.escape(name) + r'(?![A-Za-z0-9_])', val, code)

    return code


def parse_gel_code(code: str, base_dir: str = ".", include_dirs=()):
    processed_code = preprocess_gel_code(code, base_dir, include_dirs)
    parser = load_parser()
    try:
        tree = parser.parse(processed_code)
        return tree
    except UnexpectedToken as e:
        print(f"[Syntax Error] Unexpected token '{e.token}' at line {e.line}, column {e.column}")
        print(f"Context:\n{e.get_context(processed_code)}")
        return None
    except UnexpectedInput as e:
        print(f"[Syntax Error] Unexpected input at line {e.line}, column {e.column}")
        print(f"Context:\n{e.get_context(processed_code)}")
        return None