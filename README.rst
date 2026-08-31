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
