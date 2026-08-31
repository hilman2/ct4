
'''
Provides dummy Transaction and Response classes is used by Cheetah in place
of real Webware transactions when the Template obj is not used directly as a
Webware servlet.

Warning: This may be deprecated in the future, please do not rely on any
specific DummyTransaction or DummyResponse behavior
'''

import logging


class DummyResponseFailure(Exception):
    pass


class DummyResponse(object):
    '''
    A dummy Response class is used by Cheetah in place of real Webware
    Response objects when the Template obj is not used directly as a Webware
    servlet
    '''
    def __init__(self):
        self._outputChunks = []
        # A generated template does `write = trans.response().write` once
        # and then calls it for every constant and every placeholder: a
        # page of 200 rows makes over 1600 calls. Going through the
        # method below costs a Python frame each time for what is one
        # list append. Binding the append itself onto the instance
        # removes that frame; measured 0.052 against 0.023 ms for 1400
        # writes on 31-Aug-2026.
        #
        # Only where nobody has overridden write. A subclass that
        # defines its own must keep it, and an instance attribute would
        # silently shadow it.
        if type(self).write is DummyResponse.write:
            self.write = self._outputChunks.append

    def flush(self):
        pass

    def safeConvert(self, chunk):
        # Exceptionally gross, but the safest way
        # I've found to ensure I get a legit unicode object
        if not chunk:
            return u''
        if isinstance(chunk, str):
            return chunk
        try:
            return chunk.decode('utf-8', 'strict')
        except UnicodeDecodeError:
            try:
                return chunk.decode('latin-1', 'strict')
            except UnicodeDecodeError:
                return chunk.decode('ascii', 'ignore')
        except AttributeError:
            return str(chunk, errors='ignore')
        return chunk

    def write(self, value):
        self._outputChunks.append(value)

    def writeln(self, txt):
        self.write(txt)
        self.write('\n')

    def getvalue(self, outputChunks=None):
        chunks = outputChunks or self._outputChunks
        try:
            return u''.join(chunks)
        except UnicodeDecodeError:
            logging.debug(
                'Trying to work around a UnicodeDecodeError in getvalue()')
            logging.debug(
                '...perhaps you could fix "%s" while you\'re debugging')
            return ''.join((self.safeConvert(c) for c in chunks))

    def writelines(self, *lines):
        # not used
        [self.writeln(ln) for ln in lines]


class DummyTransaction(object):
    '''
        A dummy Transaction class is used by Cheetah in place of real Webware
        transactions when the Template obj is not used directly as a Webware
        servlet.

        It only provides a response object and method.  All other methods and
        attributes make no sense in this context.
    '''
    def __init__(self, *args, **kwargs):
        self._response = None

    def response(self, resp=None):
        if self._response is None:
            self._response = resp or DummyResponse()
        return self._response


class TransformerResponse(DummyResponse):
    def __init__(self, *args, **kwargs):
        super(TransformerResponse, self).__init__(*args, **kwargs)
        self._filter = None

    def getvalue(self, **kwargs):
        output = super(TransformerResponse, self).getvalue(**kwargs)
        if self._filter:
            _filter = self._filter
            if isinstance(_filter, type):
                _filter = _filter()
            return _filter.filter(output)
        return output


class TransformerTransaction(object):
    def __init__(self, *args, **kwargs):
        self._response = None

    def response(self):
        if self._response:
            return self._response
        return TransformerResponse()
