"""

TODO:
    * Display for verification
      > Highlighting

    * Command line args (see notes below)
    * Documentation
    * Blacklist

"""
# EXPORTS
__all__ = ('BibDL', )

# IMPORTS (standard)
from io import open
from os.path import join as join_path
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
    ]


## CODE ##
class BibDL(object):
    """
    """
    def __init__(self, prefix='/tmp', verbose=True):
        self.prefix = prefix
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
        authors = self.authors(key)
        authors = re.split(',(?:\s*and)?\s*', authors)
        return ', '.join(authors[:NUM_AUTHORS])

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
            self.status.result('URL', art.attrs['url'][0])

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
                        self.status.result('Cluster', art.attrs['cluster_id'][0])
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
            title = self.title(key)
            self.status.query('Title', title)
            self.status.query('Authors', self.main_authors(key))
            self.status.query('Year', self.year(key))
            url = self.pdf_url(title)
            if url is not None:
                key = unicodedata.normalize('NFKD', key).encode('ascii', 'ignore')
                dst = join_path(prefix, "{}.pdf".format(key))
                #urlretrieve(url, dst)
                self.status.result('Copied to', dst)
            else:
                print e.message

        self.status.finished()

    def all(self, prefix=None):
	"""Fetch all keys.
	"""
        prefix = prefix is None and self.prefix or prefix
        for key in self.bib.keys():
            self.single(key, prefix)
            timeout = 0.0
            while timeout <= MIN_TIMEOUT:
                timeout = normalvariate(TIMEOUT, 0.25)

            time.sleep(timeout)


## HELPERS ##

class Status(object):
    """Console colors.
    """
    BOLD    = "\033[1m" # bold
    ENDC    = "\033[0m" # end
    ERROR   = "\033[91m" # Red
    TITLE   = "\033[93m" # yellow
    KEY     = "\033[94m" # BLUE

    def __init__(self, verbose=True):
        self.verbose = verbose
        self.strcollapse = re.compile('[\d\w]+')
        self.msg_query  = {}
        self.msg_result = {}

    def similar(self, fst, snd):
        # case and spaces
        fst, snd = map(unicode.strip, map(unicode.lower, (fst, snd)))
        # remove all but letters and digits
        fst = u''.join(self.strcollapse.findall(fst))
        snd = u''.join(self.strcollapse.findall(snd))
        return fst == snd

    def query(self, key, text):
        if text is None: return
        text = unicode(text)
        self.msg_query[key] = text
        self.status(key, text)

    def result(self, key, text):
        if text is None: return
        text = unicode(text)

        if key in self.msg_query:
            # Check against query
            if not self.similar(self.msg_query[key], text):
                self.status(key, text)
                self.warning(key + ' mismatch Q')
        elif key in self.msg_result:
            # Check against former result
            if not self.similar(self.msg_result[key], text):
                self.status(key, text)
                self.warning(key + ' mismatch R')
        else:
            # Key not seen yet
            self.msg_result[key] = text
            self.status(key, text)

    def warning(self, text):
        self.status('WARNING', text, error=True)

    def error(self, text):
        self.status('ERROR', text, error=True)

    def title(self, text):
        self.finished()
        self.status('Processing', text, title=True)

    def finished(self):
        self.msg_query.clear()
        self.msg_result.clear()

    def status(self, key, text, title=False, error=False):
	"""Nicely formatted status messages.
	"""
        if text is None or (not self.verbose and not error):
            return

        s = ''
        if title:
            s += '\n'
            s += self.BOLD + key
            s += ' '
            s += self.TITLE + text
            s += self.ENDC
        else:
            s += error and self.ERROR or self.KEY
            s += '  '
            s += key.ljust(STATUS_LEN)
            s += not error and self.ENDC or ''
            s += ': '
            s += text
            s += self.ENDC

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
