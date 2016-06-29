#! /usr/bin/env python
"""Bibliography Downloader.
This module grabs an alpha style bibliography from a file and tries
to download the respective PDFs via google scholar.
"""
# Copyright (c) 2016, Matthias Baumgartner
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from
#    this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF
# THE POSSIBILITY OF SUCH DAMAGE.

# EXPORTS
__all__ = ('BibDL', )

# IMPORTS (standard)
from io import open
import os
from os.path import join as join_path
from os.path import exists as path_exists
from os.path import dirname, isdir
from random import normalvariate
import re
import sys
import time
import unicodedata
from urllib import urlretrieve
import warnings

# IMPORTS (locals)
from scholar import ScholarQuerier, ScholarSettings, SearchScholarQuery, ClusterScholarQuery

## CONFIGURATION ##

# Timeout after each query to prevent google from blocking us
TIMEOUT = 0.5
MIN_TIMEOUT = 0.25

# Number of reported authors
NUM_AUTHORS = 3

# Length of status keys
STATUS_LEN = 12

# Blacklisted pdf urls
URL_BLACKLIST = [
      re.compile('https?://[^/]*springer')
    , re.compile('https?://[^/]*academia')
    , re.compile('https?://[^/]*semanticscholar')
    ] # TODO: Reality check


## CODE ##
class BibDL(object):
    """Bibliography Downloader.

    Parses an alpha style bibliography from a file and
    allows to download one or several of its items.

    Example usage:

    >>> bibfile = '/pth/to/bibfile'
    >>> outdir  = '/tmp'
    >>> dl = BibDL(outdir)
    >>> dl.parse(bibfile)
    >>> # Download the publication for the first key
    >>> dl.single(dl.keys()[0])
    >>> # Download the publications for the first four keys
    >>> dl.some(dl.keys()[:4])
    >>> # Download all publications
    >>> dl.all()

    """
    def __init__(self, prefix='/tmp', verbose=True, overwrite=False):
        self.prefix = prefix
        self.overwrite = overwrite
        self.status = Status(verbose)

        # Bibliography
        self._re_all = re.compile('^\[(.*)\]\s*(.*?[\w?)]{2})\.\s*(.*?)\.\s*(.*)$')
        self.bib = {} # Triplet (authors, title, pub)

        # Scholar
        self.querier = ScholarQuerier()
        settings = ScholarSettings()
        self.querier.apply_settings(settings)

    def authors(self, key):
	"""Return the authors part of *key*.
	"""
        return self.bib[key][0]

    def title(self, key):
	"""Return the title part of *key*.
	"""
        return self.bib[key][1]

    def pub(self, key):
	"""Return the publication part of *key*.
	"""
        return self.bib[key][2]

    def year(self, key):
        """Return the year of publication *key*.
        """
        pub = self.pub(key)
        r = re.findall('(?:^|\D)(\d{4})(?:\D|$)', pub)
        return len(r) > 0 and r[-1] or None

    def main_authors(self, key):
        """Return the main authors.
        Produces a comma seperated list of names.
        """
        authors = self.authors(key)
        authors = re.split(',(?:\s*and)?\s*', authors)
        return ', '.join(authors[:NUM_AUTHORS])

    def keys(self):
        """Return all known keys.
        """
        return self.bib.keys()

    def clear(self):
	"""Start anew. Clears the bibliography.
	"""
        self.bib.clear()

    def parse(self, path):
	"""Parse a bibliography.
	Create a triple (authors, title, pub) for each entry.
	"""
        lines = map(unicode.strip, open(path, encoding='utf-8').readlines())
        lines = filter(lambda s: len(s) > 0, lines)

        # Parse the lines
        for s in lines:
            r = self._re_all.search(s)
            if r is None:
                continue

            key, authors, title, pub = r.groups()
            if key in self.bib:
                warnings.warn('Duplicate key {}'.format(key))

            self.bib[key] = (authors, title, pub)

    def pdf_url(self, phrase):
	"""Fetch a paper by *phrase* (usually the title) from scholar.google.com
	Tries to download a valid PDF. Returns the pdf url if one is found.
	Return None in case of errors.

        TODO: Actually check if the file can be downloaded... if error, continue with next candidate

	"""
        # Run initial query
        query = SearchScholarQuery()
        query.set_phrase(phrase) # --phrase "<phrase>"
        query.set_num_page_results(1) # -c 1
        self.querier.send_query(query)

        if len(self.querier.articles) == 0: return None # Absolutely nothing returned; Abort

	# Initial PDF url
        art = self.querier.articles[0]
        pdf_url = strip_url(art.attrs['url_pdf'][0])

	# Some status
        self.status.result('Title', art.attrs['title'][0])
        self.status.result('Year', art.attrs['year'][0])
        self.status.result('PDF', pdf_url)

	# Check PDF url
        if pdf_url is None or is_blacklisted(pdf_url):
            #self.status.result('URL', art.attrs['url'][0])

            # Article found, but no PDF. Resort to searching by cluster.
            if art.attrs['cluster_id'][0] is not None:
                cluster = ClusterScholarQuery(cluster=art.attrs['cluster_id'][0])
                self.querier.send_query(cluster)

		# Walk through results
                for cart in self.querier.articles:
                    curl = strip_url(cart.attrs['url_pdf'][0])
                    if curl is not None and not is_blacklisted(curl):
			# Valid PDF found!
                        pdf_url = curl
			# More status
                        #self.status.result('Cluster', art.attrs['cluster_id'][0])
                        self.status.result('Title', cart.attrs['title'][0])
                        self.status.result('Year', cart.attrs['year'][0])
                        self.status.result('PDF', pdf_url)
			# We have a result, abort search
                        break

                # pdf_url can stil be None

        if is_book(pdf_url) or is_book(art.attrs['url'][0]):
            self.status.warning('Might be a book')

        return pdf_url

    def single(self, key, prefix=None):
	"""Fetch *key*.
	The file is stored in directory *prefix*, with file name *key*.pdf
	"""
        prefix = prefix is None and self.prefix or prefix

        self.status.title(key)

        try:
            outkey = unicodedata.normalize('NFKD', key).encode('ascii', 'ignore')
            dst = join_path(prefix, "{}.pdf".format(outkey))
            if not path_exists(dirname(dst)) or not isdir(dirname(dst)) or not os.access(dirname(dst), os.W_OK): # Directory access
                raise Exception('Cannot write to directory')

            if path_exists(dst): # File access
                if not self.overwrite:
                    raise Exception('File exists already, not overwriting')
                elif not os.access(dst, os.W_OK):
                    raise Exception('Cannot overwrite file.')
                else:
                    self.status.warning('Overwriting file')

            title = self.title(key)
            self.status.query('Title', title)
            self.status.query('Authors', self.main_authors(key))
            self.status.query('Year', self.year(key))
            url = self.pdf_url(title)
            if url is not None:
                urlretrieve(url, dst)
                self.status.result('Copied to', dst)
            else:
                self.status.error('No PDF found')
        except Exception, e:
            self.status.error(e.message)

        self.status.finished()

    def some(self, keys, prefix=None):
        """Fetch some keys.
        """
        prefix = prefix is None and self.prefix or prefix
        for key in keys:
            self.single(key, prefix)
            timeout = 0.0
            while timeout <= MIN_TIMEOUT:
                timeout = normalvariate(TIMEOUT, 0.25)

            time.sleep(timeout)

    def all(self, prefix=None):
	"""Fetch all keys.
	"""
        self.some(self.bib.keys(), prefix)


## HELPERS ##

class Status(object):
    """Nicely formatted status messages.

    Print different types of status messages, each with
    its individual, special snowflake formatting.

    Should help distinguish origin and relevance of
    program output.

    """
    # Colors and text formatting
    ENDC    = "\033[0m"  # end
    BOLD    = "\033[1m"  # bold
    TITLE   = "\033[93m" # yellow
    ERROR   = "\033[91m" # Red
    KEY     = "\033[94m" # blue
    QUERY   = "\033[97m" # white
    RESULT  = "\033[37m" # gray

    # Status chunks
    KEY_LEN = STATUS_LEN
    SEP     = ': '
    IDENT   = '  '

    def __init__(self, verbose=True):
        self.verbose = verbose
        self.strcollapse = re.compile('[\d\w]+')
        self.msg_query  = {}
        self.msg_result = {}

    def _similar(self, fst, snd):
        """Return True if the strings *fst* and *snd* are similar.
        """
        # case and spaces
        fst, snd = map(unicode.strip, map(unicode.lower, (fst, snd)))
        # remove all but letters and digits
        fst = u''.join(self.strcollapse.findall(fst))
        snd = u''.join(self.strcollapse.findall(snd))
        return fst == snd

    def title(self, text):
        """Title. Starts a new block.
        """
        self.msg_query.clear()
        self.msg_result.clear()
        print self.BOLD + 'Processing ' + self.TITLE + text + self.ENDC

    def finished(self):
        """Finish a block.
        """
        print ""

    def error(self, text):
        """Error message.
        """
        if text is None: return
        print self.BOLD + self.ERROR + self.IDENT + 'ERROR'.ljust(self.KEY_LEN) + self.SEP + text + self.ENDC

    def warning(self, text):
        """Warning.
        """
        if text is None or not self.verbose: return
        print self.ERROR + self.IDENT + 'WARNING'.ljust(self.KEY_LEN) + self.SEP + text + self.ENDC

    def query(self, key, text):
        """Query information.
        """
        if text is None or not self.verbose: return
        text = unicode(text)
        self.msg_query[key] = text
        self._status(key, text, self.QUERY)

    def result(self, key, text):
        """Result information.
        """
        if text is None or not self.verbose: return
        text = unicode(text)

        warn = None
        if key in self.msg_query:
            # Check against query
            if not self._similar(self.msg_query[key], text):
                warn = 'mismatch query'
                self._status(key, text, self.RESULT, warn=warn)
        elif key in self.msg_result:
            # Check against former result
            if not self._similar(self.msg_result[key], text):
                warn = 'mismatch former result'
                self._status(key, text, self.RESULT, warn=warn)
        else:
            # Key not seen yet
            self.msg_result[key] = text
            self._status(key, text, self.RESULT, warn=warn)


    def _status(self, key, text, color, warn=None):
	"""Print status message.
        Append an inline warning if *warn* is given.
	"""
        if text is None or not self.verbose: return

        warn = warn is not None and ' ({})'.format(warn) or ''

        s  = self.KEY + self.IDENT + key.ljust(self.KEY_LEN) + self.ENDC
        s += self.SEP
        s += color + text + self.ENDC
        s += self.ERROR + warn + self.ENDC
        print s

def is_book(url):
    """Check if *url* hints a book.
    """
    return url is None or re.match('https?://[^/]*books.google.com', url) is not None

def is_blacklisted(url):
    """Check if *url* is blacklisted.
    """
    for rx in URL_BLACKLIST:
        if rx.search(url) is not None:
            return True
    return False

def strip_url(url):
    """Strip google part from URL.
    """
    if url is None: return None
    m = re.search('scholar\.google\.com\/(http.*)', url)
    url = m is not None and m.groups()[0] or url
    return url

## MAIN ##

def main():
    import optparse
    usage = """bibdl.py /path/to/bibliography.t
Download a complete bibliography"""

    # TODO: Recursive operation / Multiple files
    # TODO: Fetch single key via command line arg
    # TODO: Only overwrite if --force given
    # TODO: Output directory
    # TODO: Bibliography file documented in the help text

    fmt = optparse.IndentedHelpFormatter()
    parser = optparse.OptionParser(usage=usage, formatter=fmt)
    parser.add_option('-k', '--key', metavar='KEY', default=None, help='Fetch key only')
    #parser.add_option('-v', '--verbose', metavar='VERBOSE', default=None, help='Print status messages')
    #parser.add_option('-o', '--out', metavar='OUT', default=None, help='Output directory')

    options, paths = parser.parse_args()

    if len(paths) == 0:
        parser.error("At least one bibliography file is required.")

    dl = BibDL(prefix='/tmp/bibdl_p250/pdfs', verbose=True)

    for p in paths:
        dl.parse(p)

    if options.key is not None:
        dl.single(unicode(options.key))
    else:
        dl.all()

    #import code
    #code.interact(local=locals())

    print ""

    return 0

if __name__ == '__main__':
    sys.exit(main())

## EOF ##
