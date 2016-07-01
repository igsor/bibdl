#! /usr/bin/env python
"""Bibliography Downloader.
This module grabs an alpha style bibliography from a file and tries
to download the respective PDFs via google scholar.

Here's an example of the alpha style bibliography (without the line breaks)

[ASS96]         Harold Abelson, Gerald J. Sussman, and Julie Sussman.
                Structure and Interpretation of Computer Programs.
                MIT Press, Cambridge, second edition, 1996.

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
import os
import re
import sys
import time
import unicodedata
import warnings
from commands import getstatusoutput
from datetime import datetime
from io import open
from os.path import dirname, isdir
from os.path import exists as path_exists
from os.path import join as join_path
from random import normalvariate, randint
from urllib import urlretrieve
from user_agent import generate_user_agent

# IMPORTS (locals)
from scholar import ClusterScholarQuery, ScholarConf, ScholarQuerier, ScholarSettings, SearchScholarQuery

## CONFIGURATION ##

# Timeout after each query to prevent google from blocking us
TIMEOUT = 5.0
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
    def __init__(self, prefix='/tmp', verbose=True, overwrite=False, logfile=None, blocked_cmd=None):
        self.prefix = prefix
        self.overwrite = overwrite
        self.status = Status(verbose, logfile)
        self.timeout = TIMEOUT
        self.blocked_cmd = blocked_cmd

        # Bibliography
        self._re_all = re.compile('^\[(.*)\]\s*(.*?[\w?)]{2})\.\s*(.*?)[.!?]\s*(.*)$')
        self.bib = {} # Triplet (authors, title, pub)

        # Scholar
        self.querier = BibDLQuerier() # ScholarQuerier()
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

    def on_blocked(self):
        ScholarConf.USER_AGENT = generate_user_agent() # Randomize user agent
        self.timeout *= 2.0 # Increase timeout (exponential backoff)

        if self.blocked_cmd is not None:
            status, output = getstatusoutput(self.blocked_cmd)
            if status != 0:
                self.status.error(output)

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

        if len(self.querier.articles) == 0:
            self.status.warning('No search results. Blocked maybe?')
            # TODO: Open result page in a browser (to answer the captcha)
            self.on_blocked()
            return None # Absolutely nothing returned; Abort

        self.timeout = TIMEOUT

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
        has_queried = False

        try:
            outkey = encode(key)
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
            has_queried = True
            if url is not None:
                urlretrieve(url, dst)
                self.status.result('Copied to', dst)
            else:
                self.status.error('No PDF found')
        except Exception, e:
            self.status.error(e.message)

        self.status.finished()
        return has_queried

    def some(self, keys, prefix=None):
        """Fetch some keys.
        """
        prefix = prefix is None and self.prefix or prefix
        for key in keys:
            dowait = self.single(key, prefix)
            if dowait:
                timeout = 0.0
                while timeout <= MIN_TIMEOUT:
                    timeout = normalvariate(self.timeout, 0.25)

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

    def __init__(self, verbose=True, logfile=None):
        self.verbose = verbose
        self.strcollapse = re.compile('[\d\w]+')
        self.logfile = logfile is not None and open(logfile, 'a') or None
        if self.logfile is not None: self.logfile.write(u'\n{}\n'.format(datetime.now().isoformat(' ')))
        self.msg_query  = {}
        self.msg_result = {}

    def _writeln(self, line):
        print line
        if self.logfile is not None:
            self.logfile.write(line + u'\n')
            self.logfile.flush()

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
        self._writeln(self.BOLD + 'Processing ' + self.TITLE + text + self.ENDC)

    def finished(self):
        """Finish a block.
        """
        if self.verbose:
            self._writeln("")

    def error(self, text):
        """Error message.
        """
        if text is None: return
        self._writeln(self.BOLD + self.ERROR + self.IDENT + 'ERROR'.ljust(self.KEY_LEN) + self.SEP + text + self.ENDC)

    def warning(self, text):
        """Warning.
        """
        if text is None or not self.verbose: return
        self._writeln(self.ERROR + self.IDENT + 'WARNING'.ljust(self.KEY_LEN) + self.SEP + text + self.ENDC)

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
                warn = 'differs from query'
                self._status(key, text, self.RESULT, warn=warn)
        elif key in self.msg_result:
            # Check against former result
            if not self._similar(self.msg_result[key], text):
                warn = 'differs from former result'
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
        self._writeln(s)

def encode(text):
    return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore')

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

class BibDLQuerier(ScholarQuerier):
    """Randomize user agent every N queries.
    N is chosen randomly.
    """
    def __init__(self):
        super(BibDLQuerier, self).__init__()
        self.queries_sent = 0
        self.queries_change = randint(5, 15)

    def send_query(self, query):
        # TODO: Randomize query, i.e. remove/change unused arguments to vary query signature
        self.queries_sent += 1
        if self.queries_sent % self.queries_change == 0:
            self.queries_change = randint(3, 13)
            ScholarConf.USER_AGENT = generate_user_agent()

        return super(BibDLQuerier, self).send_query(query)

## MAIN ##

def main():
    # Local imports
    import optparse
    from os.path import basename, splitext

    usage = """bibdl.py [OPTIONS] /pth/to/bibA.bib [/pth/to/bibB.bib ...]
Download a complete bibliography.

The bibliography file consists of entries like the following:

[ASS96]     Harold Abelson, Gerald J. Sussman, and Julie Sussman. Structure and Interpretation of Computer Programs. MIT Press, Cambridge, second edition, 1996.

The output is colored, if your terminal supports it. The output gives query
values in white font. Retrieved values are in gray and are printed only if
they add information (i.e. are not shown yet or differ from previous values).

Examples:

# Copy all publications in bibfile.bib into the current working directory.
bibdl.py /pth/to/bibfile.bib

# Only fetch the publication ASS96
bibdl.py -k ASS96 /pth/to/bibfile.bib

# Same but store the pdf in /tmp/pubs
bibdl.py -o /tmp/pubs -k ASS96 /pth/to/bibfile.bib

# Fetch all publications and put them into /tmp/pubs
bibdl.py -o /tmp/pubs /pth/to/bibA.bib /pth/to/bibB.bib /pth/to/bibC.bib

# Copy the bibliographies, each into its own directory
# e.g. publications listed in bibA.bib go to /tmp/pubs/bibA
bibdl.py -o /tmp/pubs /pth/to/bibA.bib /pth/to/bibB.bib /pth/to/bibC.bib

# Get a list of keys and titles
bibdl.py -r "get('title')" /pth/to/bibA.bib"""

    fmt = optparse.IndentedHelpFormatter()
    parser = optparse.OptionParser(usage=usage, formatter=fmt)
    parser.add_option('-f', '--force', action='store_true', dest='force', default=False, help='Overwrite existing files')
    parser.add_option('-k', '--key', metavar='KEY', default=None, help='Fetch key only') # TODO: Allow several, comma-seperated keys
    parser.add_option('-o', '--out', metavar='OUTPUT', default=os.getcwd(), help='Output directory')
    parser.add_option('-q', '--quiet', action='store_false', dest='verbose', default=True, help='Don\'t print status messages')
    parser.add_option('-s', '--separate', action='store_true', dest='separate', default=False, help='One directory per bibliography file')
    parser.add_option('-c', '--code', action='store_true', dest='code', default=False, help='Don\'t download but instead open a python shell after loading the bib file')
    parser.add_option('-r', '--run', metavar='CODE', default='', help='Run CODE and exit.')
    parser.add_option('-l', '--log', metavar='LOGFILE', default=None, help='Write status to LOGFILE')
    parser.add_option('--blocked', metavar='COMMAND', default=None, help='Command to be executed in case we\'re blocked by google')
    options, paths = parser.parse_args()

    if len(paths) == 0:
        parser.error("At least one bibliography file is required.")

    def split_out(bib):
        """Return a (valid) path for separated bibliographies."""
        outpath = join_path(options.out, splitext(basename(bib))[0])
        if not path_exists(outpath):
            os.mkdir(outpath)
        return outpath

    def print_error(msg):
        """Print an error message."""
        print "\033[1m\033[91mERROR: " + msg + "\033[0m"

    try:
        # Create instance
        dl = BibDL(
              prefix=options.out
            , verbose=options.verbose
            , overwrite=options.force
            , logfile=options.log
            , blocked_cmd=options.blocked
            )

        if options.code or options.run != '':
            for bib in paths:
                try:
                    if not os.path.exists(bib):
                        raise Exception('Cannot read {}'.format(bib))
                    dl.parse(bib)
                except OSError, e:
                    print_error(e.filename + ': ' + e.strerror)
                except Exception, e:
                    print_error(e.message)

            def get(arg, keys=None):
                """Access to parsed values."""
                fu = {'author': dl.authors
                     ,'title' : dl.title
                     ,'pub'   : dl.pub
                     ,'year'  : dl.year
                     ,'all'   : lambda k: dl.authors(k) + '. ' + dl.title(k) + '. ' + dl.pub(k)
                     }[arg]
                if keys is None:
                    keys = dl.keys()
                for k in keys:
                    print encode(k).ljust(12), encode(fu(k))

            if options.run != '':
                eval(options.run, globals(), locals())

            if options.code:
                import code
                code.interact(local=locals(), banner='Happy hacking')

            return 0

        # Check the target directory
        # TODO: Create directories only if needed (i.e. within BibDL
        # TODO: Create directories anyways, also if --force not present
        #       otherwise, --force has two meanings; create dirs and overwrite files
        if not path_exists(options.out):
            if options.force:
                os.makedirs(options.out)
            else:
                raise Exception('Output directory {} does not exist'.format(options.out))

        elif not isdir(options.out):
            raise Exception('Output {} is not a directory'.format(options.out))

        elif not os.access(options.out, os.W_OK):
            raise Exception('Output {} cannot be written'.format(options.out))

        # Do the actual work
        if options.key is not None:
            # Search a single file
            for bib in paths:
                try:
                    if not os.path.exists(bib):
                        raise Exception('Cannot read {}'.format(bib))

                    dl.parse(bib)
                    if options.key in dl.keys():
                        prefix = options.separate and split_out(bib) or None
                        dl.single(unicode(options.key), prefix=prefix)
                        break

                except OSError, e:
                    print_error(e.filename + ': ' + e.strerror)
                except Exception, e:
                    print_error(e.message)

        else:
            # Fetch whole bibliography
            for bib in paths:
                try:
                    if not os.path.exists(bib):
                        raise Exception('Cannot read {}'.format(bib))

                    dl.parse(bib)

                    if options.separate:
                        dl.all(prefix=split_out(bib))
                        dl.clear() # Cleanup after processing items
                except OSError, e:
                    print_error(e.filename + ': ' + e.strerror)
                except Exception, e:
                    print_error(e.message)

            if not options.separate:
                dl.all()

    except OSError, e:
        print_error(e.filename + ': ' + e.strerror)
    except Exception, e:
        print_error(e.message)

    return 0

if __name__ == '__main__':
    sys.exit(main())

## EOF ##
