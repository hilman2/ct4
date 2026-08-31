"""The language layer: lexer, tree, code generation.

Built underneath the existing compiler, not beside it. Every layer
carries an assertion that holds over the whole corpus, and the layer
above it is only started once the one below it holds. A rewrite of
Cheetah's parser that goes for the finish line in one leap does not
arrive, and there would be no way to tell how far along it was.
"""
