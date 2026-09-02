"""A template that spells its tokens differently.

Two real templates do: a LaTeX resume writes ";name" and "!if", a
bash completion writes "~name". Every case that renders is rendered
by ct3 as well and compared, because a token is only honoured if the
page comes out the same.
"""

from __future__ import annotations

import pytest
from test_settings import both

from ct4.lang import codegen, lex, tree

SEMI = {"cheetahVarStartToken": ";", "directiveStartToken": "!"}
# "rows" and not "items": a dict namespace answers "items" with its
# own method, in ct3 as here.
CONTEXT = {"name": "World", "rows": [1, 2], "flag": True,
           "obj": type("Obj", (), {"attr": "A"})()}


def test_the_var_token_and_the_directive_token():
    assert both("!for ;item in ;rows\n- ;item\n!end for\n", CONTEXT,
                SEMI) == "- 1\n- 2\n"
    assert both(";name ;obj.attr ;{name}\n", CONTEXT, SEMI) == \
        "World A World\n"
    assert both("!if ;flag\nyes\n!else\nno\n!end if\n", CONTEXT, SEMI) == \
        "yes\n"


def test_the_old_tokens_are_text_and_the_new_one_escapes():
    assert both("$5 and #x stay text, ;name\n", CONTEXT, SEMI) == \
        "$5 and #x stay text, World\n"
    assert both("\\;name and \\!if\n", CONTEXT, SEMI) == ";name and !if\n"


def test_the_end_token_and_the_comment_keep_their_spelling():
    # Only the two tokens changed; the directive end token and the
    # comment are still ct3's defaults.
    assert both("!if 1#yes!end if#\n", CONTEXT, SEMI) == "yes\n"
    assert both("## gone\n;name\n", CONTEXT, SEMI) == "World\n"


def test_a_comment_token_of_its_own():
    settings = {"commentStartToken": "//"}
    assert both("// note\nhi $name ## still text\n", CONTEXT, settings) == \
        "hi World ## still text\n"


def test_psp_and_block_comment_tokens():
    settings = {"PSPStartToken": "<?", "PSPEndToken": "?>"}
    assert both("<? write('x') ?>\n", CONTEXT, settings) == "x\n"
    settings = {"multiLineCommentStartToken": "/*",
                "multiLineCommentEndToken": "*/"}
    assert both("a/* hidden */b\n", CONTEXT, settings) == "ab\n"


def test_the_resume_shape_sets_its_tokens_at_the_head():
    source = ("## Change delimiters for Cheetah\n#compiler-settings\n"
              "cheetahVarStartToken = ;\ndirectiveStartToken = !\n"
              "#end compiler-settings\n\n!for ;x in ;rows\n;x\n!end for\n")
    assert both(source, CONTEXT) == "\n1\n2\n"


def test_the_bash_shape_may_open_with_a_shell_comment():
    # A line of plain text before the settings, holding none of ct3's
    # tokens and none of the new ones, stays text.
    source = ("# bash completion for the client\n\n## note\n"
              "#compiler-settings\ncheetahVarStartToken = ~\n"
              "#end compiler-settings\necho $HOME ~name\n")
    assert both(source, CONTEXT) == \
        "# bash completion for the client\n\necho $HOME World\n"


def test_text_before_the_settings_that_the_new_tokens_read_is_refused():
    # Text under ct3's tokens, a placeholder under the new one.
    source = ("call ;here\n#compiler-settings\n"
              "cheetahVarStartToken = ;\n#end compiler-settings\n")
    with pytest.raises(codegen.Unsupported) as error:
        codegen.generate(source)
    assert "text before" in str(error.value)


def test_raw_is_refused_under_changed_tokens():
    with pytest.raises(codegen.Unsupported) as error:
        codegen.generate("!raw\nx\n!end raw\n", SEMI)
    assert "#raw" in str(error.value)


def test_the_lexer_reads_the_tokens_it_is_given():
    names = tree.syntax(tokens=lex.Tokens(var=";", directive="!"))
    kinds = [t.kind for t in lex.tokens(";a !if 1#x!end if#\n", names)]
    assert kinds == [lex.PLACEHOLDER, lex.TEXT, lex.DIRECTIVE, lex.TEXT,
                     lex.DIRECTIVE_END, lex.TEXT, lex.DIRECTIVE, lex.TEXT,
                     lex.DIRECTIVE_END, lex.TEXT]
    root = tree.parse("!if 1\n;a\n!end if\n", names)
    assert [(n.kind, n.name) for n in root.children] == [(tree.BLOCK, "if")]
