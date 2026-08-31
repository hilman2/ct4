Cheetah Template 4.0.0a0
========================

Cheetah 4 is a Python template engine and code generation tool, with
its weight on evaluating data and producing JSON. It is a fork of
Cheetah3, and it renders the templates of Cheetah3 byte for byte the
same.

Python 3.10 or newer is required.

Free software under the GNU Lesser General Public License, version 3
or later. The code inherited from Cheetah3 stays available under the
MIT license from its own project; see LICENSE for both.


Installing, and CT3
===================

The distribution is called ``Cheetah4``. The package you import is
called ``Cheetah``, as it was before, because that is what every
existing template stack expects::

    pip install Cheetah4
    python -c "import Cheetah; print(Cheetah.Version)"

``ct3``, the distribution Cheetah3 is published under, installs a
package called ``Cheetah`` as well. Only one of them can be on disk at
a time, and pip does not warn about it. Three things follow, all of
them measured rather than assumed:

Installing Cheetah4 over ct3 works and gives you the Cheetah4 engine.
Both distributions are then listed as installed, and both believe they
own the ``Cheetah`` package.

Uninstalling ct3 afterwards **breaks Cheetah4**. The file list ct3
recorded still names ``Cheetah/``, so pip removes files that Cheetah4
now owns, and leaves an installation it reports as intact. Reinstall
with ``pip install --force-reinstall --no-deps Cheetah4``.

Installing anything that depends on ct3 puts the older engine back,
silently. weewx declares ``CT3>=3.1``, so installing or upgrading weewx
does exactly this. Everything keeps working, on the engine Cheetah4
forked away from.

The ``ct4`` command refuses to run when it finds an engine older than
4, and says what to do. weewx does not call it, so on a station the
check is yours to make::

    python -c "import Cheetah; print(Cheetah.Version)"

A virtual environment for weewx that never sees ct3 avoids all of it.


Where is CheetahTemplate3
=========================

Site:
https://cheetahtemplate.org/

Download:
https://pypi.org/project/ct3/

News and changes:
https://cheetahtemplate.org/news.html

StackOverflow:
https://stackoverflow.com/questions/tagged/cheetah

Mailing lists:
https://sourceforge.net/p/cheetahtemplate/mailman/

Development:
https://github.com/CheetahTemplate3

Developer Guide:
https://cheetahtemplate.org/dev_guide/


Example
=======

Install::

    $ pip install ct3

Below is a simple example of some Cheetah code, as you can see it's practically
Python. You can import, inherit and define methods just like in a regular Python
module, since that's what your Cheetah templates are compiled to :) ::

    #from Cheetah.Template import Template
    #extends Template

    #set $people = [{'name' : 'Tom', 'mood' : 'Happy'}, {'name' : 'Dick',
                            'mood' : 'Sad'}, {'name' : 'Harry', 'mood' : 'Hairy'}]

    <strong>How are you feeling?</strong>
    <ul>
        #for $person in $people
            <li>
                $person['name'] is $person['mood']
            </li>
        #end for
    </ul>
