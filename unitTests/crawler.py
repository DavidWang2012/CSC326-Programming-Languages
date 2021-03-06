
# Copyright (C) 2011 by Peter Goodman
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import urllib2
import urlparse
from BeautifulSoup import *
from collections import defaultdict
import re
import pagerank as pr
import sqlite3 as lite
from getresults import getResults


def attr(elem, attr):
    """An html attribute from an html element. E.g. <a href="">, then
    attr(elem, "href") will get the href or an empty string."""
    try:
        return elem[attr]
    except:
        return ""

WORD_SEPARATORS = re.compile(r'\s|\n|\r|\t|[^a-zA-Z0-9\-_]')

class crawler(object):
    """Represents 'Googlebot'. Populates a database by crawling and indexing
    a subset of the Internet.

    This crawler keeps track of font sizes and makes it simpler to manage word
    ids and document ids."""

    def __init__(self, db_conn, url_file):
        """Initialize the crawler with a connection to the database to populate
        and with the file containing the list of seed URLs to begin indexing."""
        self._url_queue = [ ]
        self._doc_id_cache = { }
        self._word_id_cache = { }
    
        # (Sai) Data structures for function backend
        self._document_index = []
        self._lexicon = {}
        self._inverted_index = defaultdict(set)
        self._links = {}

        # (Sai) Redis connection

   
        # (Sai) Persistent Store database tables
        self._db_conn = db_conn
        self._cur = self._db_conn.cursor()
        self._cur.execute('''CREATE TABLE IF NOT EXISTS documentIndex(docid INTEGER PRIMARY KEY, url TEXT);''')
        self._cur.execute('''CREATE TABLE IF NOT EXISTS lexicon(wordid INTEGER PRIMARY KEY, word TEXT);''')
        self._cur.execute('''CREATE TABLE IF NOT EXISTS invertedIndex(entryno INTEGER PRIMARY KEY, wordid INTEGER, docid INTEGER);''')
        self._cur.execute('''CREATE TABLE IF NOT EXISTS pageRanks(docid INTEGER PRIMARY KEY, pagerank REAL);''')

        # functions to call when entering and exiting specific tags
        self._enter = defaultdict(lambda *a, **ka: self._visit_ignore)
        self._exit = defaultdict(lambda *a, **ka: self._visit_ignore)

        # add a link to our graph, and indexing info to the related page
        self._enter['a'] = self._visit_a

        # record the currently indexed document's title an increase
        # the font size
        def visit_title(*args, **kargs):
            self._visit_title(*args, **kargs)
            self._increase_font_factor(7)(*args, **kargs)

        # increase the font size when we enter these tags
        self._enter['b'] = self._increase_font_factor(2)
        self._enter['strong'] = self._increase_font_factor(2)
        self._enter['i'] = self._increase_font_factor(1)
        self._enter['em'] = self._increase_font_factor(1)
        self._enter['h1'] = self._increase_font_factor(7)
        self._enter['h2'] = self._increase_font_factor(6)
        self._enter['h3'] = self._increase_font_factor(5)
        self._enter['h4'] = self._increase_font_factor(4)
        self._enter['h5'] = self._increase_font_factor(3)
        self._enter['title'] = visit_title

        # decrease the font size when we exit these tags
        self._exit['b'] = self._increase_font_factor(-2)
        self._exit['strong'] = self._increase_font_factor(-2)
        self._exit['i'] = self._increase_font_factor(-1)
        self._exit['em'] = self._increase_font_factor(-1)
        self._exit['h1'] = self._increase_font_factor(-7)
        self._exit['h2'] = self._increase_font_factor(-6)
        self._exit['h3'] = self._increase_font_factor(-5)
        self._exit['h4'] = self._increase_font_factor(-4)
        self._exit['h5'] = self._increase_font_factor(-3)
        self._exit['title'] = self._increase_font_factor(-7)

        # never go in and parse these tags
        self._ignored_tags = set([
            'meta', 'script', 'link', 'meta', 'embed', 'iframe', 'frame', 
            'noscript', 'object', 'svg', 'canvas', 'applet', 'frameset', 
            'textarea', 'style', 'area', 'map', 'base', 'basefont', 'param',
        ])

        # set of words to ignore
        self._ignored_words = set([
            '', 'the', 'of', 'at', 'on', 'in', 'is', 'it',
            'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j',
            'k', 'l', 'm', 'n', 'o', 'p', 'q', 'r', 's', 't',
            'u', 'v', 'w', 'x', 'y', 'z', 'and', 'or',
        ])

        # TODO remove me in real version
        self._mock_next_doc_id = 1
        self._mock_next_word_id = 1

        # keep track of some info about the page we are currently parsing
        self._curr_depth = 0
        self._curr_url = ""
        self._curr_doc_id = 0
        self._font_size = 0
        self._curr_words = None

        # get all urls into the queue
        try:
            with open(url_file, 'r') as f:
                for line in f:
                    self._url_queue.append((self._fix_url(line.strip(), ""), 0))
        except IOError:
            pass
    
    # TODO remove me in real version
    def _mock_insert_document(self, url):
        """A function that pretends to insert a url into a document db table
        and then returns that newly inserted document's id."""
        ret_id = self._mock_next_doc_id
        self._mock_next_doc_id += 1

        # (Sai) Add this docid to the document index, use temp blank title
        #(Sai) Blank string to be replaced by title later
        elem = [ret_id, str(url), ""]
        self._document_index.append(elem)

        # (Sai) Insert into documentIndex table in db
        query_str = "INSERT INTO documentIndex VALUES(%d, '%s');" % (ret_id, str(url))
        self._cur.execute(query_str)
        self._db_conn.commit()

        return ret_id
    
        # TODO remove me in real version
    def _mock_insert_word(self, word):
        """A function that pretends to inster a word into the lexicon db table
        and then returns that newly inserted word's id."""
        ret_id = self._mock_next_word_id
        self._mock_next_word_id += 1

        # (Sai) Insert into lexicon table in db
        query_str = "INSERT INTO lexicon VALUES(%d, '%s');" % (ret_id, word)
        self._cur.execute(query_str)
        self._db_conn.commit()

        return ret_id
    
    def word_id(self, word):
        """Get the word id of some specific word."""
        # (Sai) Check if in lexicon and if it is, get word id and put in cache    
        if word in self._lexicon:
            word_id = self._lexicon[word]
            self._word_id_cache[word] = word_id

        else:
            word_id = self._mock_insert_word(word)
            self._lexicon[word] = word_id
            self._word_id_cache[word] = word_id

        # (Sai) Update the inverted index with this word id and document id
        self._inverted_index[word_id].add(self._curr_doc_id)

        # (Sai) Update database table for inverted index
        query_str = "INSERT INTO invertedIndex VALUES(NULL, %d, %d);" % (word_id, self._curr_doc_id)
        self._cur.execute(query_str)
        self._db_conn.commit()

    
        return word_id
    
    def document_id(self, url):
        """Get the document id for some url."""
        if url in self._doc_id_cache:
            return self._doc_id_cache[url]
        
        # TODO: just like word id cache, but for documents. if the document
        #       doesn't exist in the db then only insert the url and leave
        #       the rest to their defaults.
        
        doc_id = self._mock_insert_document(url)
        self._doc_id_cache[url] = doc_id
        return doc_id
    
    def _fix_url(self, curr_url, rel):
        """Given a url and either something relative to that url or another url,
        get a properly parsed url."""

        rel_l = rel.lower()
        if rel_l.startswith("http://") or rel_l.startswith("https://"):
            curr_url, rel = rel, ""
            
        # compute the new url based on import 
        curr_url = urlparse.urldefrag(curr_url)[0]
        parsed_url = urlparse.urlparse(curr_url)
        return urlparse.urljoin(parsed_url.geturl(), rel)

    def add_link(self, from_doc_id, to_doc_id):
        """Add a link into the database, or increase the number of links between
        two pages in the database."""
        # TODO
        if (from_doc_id, to_doc_id) not in self._links:
            self._links[(from_doc_id, to_doc_id)] = 1
        else:
            self._links[(from_doc_id, to_doc_id)] += 1

    def _visit_title(self, elem):
        """Called when visiting the <title> tag."""
        title_text = self._text_of(elem).strip()
        #print "document title="+ repr(title_text)

        # TODO update document title for document id self._curr_doc_id
        # (Sai) Update document title in document idex:
        index_val = self._curr_doc_id - 1
        title_offset = 2
        self._document_index[index_val][title_offset] = str(title_text)
    
    def _visit_a(self, elem):
        """Called when visiting <a> tags."""

        dest_url = self._fix_url(self._curr_url, attr(elem,"href"))

        #print "href="+repr(dest_url), \
        #      "title="+repr(attr(elem,"title")), \
        #      "alt="+repr(attr(elem,"alt")), \
        #      "text="+repr(self._text_of(elem))

        # add the just found URL to the url queue
        self._url_queue.append((dest_url, self._curr_depth))
        
        # add a link entry into the database from the current document to the
        # other document
        self.add_link(self._curr_doc_id, self.document_id(dest_url))

        # TODO add title/alt/text to index for destination url
    
    def _add_words_to_document(self):
        # TODO: knowing self._curr_doc_id and the list of all words and their
        #       font sizes (in self._curr_words), add all the words into the
        #       database for this document
        #print "    num words="+ str(len(self._curr_words))
        pass

    def _increase_font_factor(self, factor):
        """Increade/decrease the current font size."""
        def increase_it(elem):
            self._font_size += factor
        return increase_it
    
    def _visit_ignore(self, elem):
        """Ignore visiting this type of tag"""
        pass

    def _add_text(self, elem):
        """Add some text to the document. This records word ids and word font sizes
        into the self._curr_words list for later processing."""
        words = WORD_SEPARATORS.split(elem.string.lower())
        for word in words:
            word = word.strip()
            if word in self._ignored_words:
                continue
            self._curr_words.append((self.word_id(word), self._font_size))
        
    def _text_of(self, elem):
        """Get the text inside some element without any tags."""
        if isinstance(elem, Tag):
            text = [ ]
            for sub_elem in elem:
                text.append(self._text_of(sub_elem))
            
            return " ".join(text)
        else:
            return elem.string

    def _index_document(self, soup):
        """Traverse the document in depth-first order and call functions when entering
        and leaving tags. When we come accross some text, add it into the index. This
        handles ignoring tags that we have no business looking at."""
        class DummyTag(object):
            next = False
            name = ''
        
        class NextTag(object):
            def __init__(self, obj):
                self.next = obj
        
        tag = soup.html
        stack = [DummyTag(), soup.html]

        while tag and tag.next:
            tag = tag.next

            # html tag
            if isinstance(tag, Tag):

                if tag.parent != stack[-1]:
                    self._exit[stack[-1].name.lower()](stack[-1])
                    stack.pop()

                tag_name = tag.name.lower()

                # ignore this tag and everything in it
                if tag_name in self._ignored_tags:
                    if tag.nextSibling:
                        tag = NextTag(tag.nextSibling)
                    else:
                        self._exit[stack[-1].name.lower()](stack[-1])
                        stack.pop()
                        tag = NextTag(tag.parent.nextSibling)
                    
                    continue
                
                # enter the tag
                self._enter[tag_name](tag)
                stack.append(tag)

            # text (text, cdata, comments, etc.)
            else:
                self._add_text(tag)

    def crawl(self, depth=2, timeout=3):
        """Crawl the web!"""
        seen = set()

        while len(self._url_queue):

            url, depth_ = self._url_queue.pop()

            # skip this url; it's too deep
            if depth_ > depth:
                continue

            doc_id = self.document_id(url)

            # we've already seen this document
            if doc_id in seen:
                continue

            seen.add(doc_id) # mark this document as haven't been visited
            
            socket = None
            try:
                socket = urllib2.urlopen(url, timeout=timeout)
                soup = BeautifulSoup(socket.read())

                self._curr_depth = depth_ + 1
                self._curr_url = url
                self._curr_doc_id = doc_id
                self._font_size = 0
                self._curr_words = [ ]
                self._index_document(soup)
                self._add_words_to_document()
                #print "    url="+repr(self._curr_url)

            except Exception as e:
                print e
                pass
            finally:
                if socket:
                    socket.close()

    # (Sai) - Functions for returning inverted index:
    def get_inverted_index(self):
        """Return the inverted index with key word id and value a set of doc ids"""
        return dict(self._inverted_index)

    def get_resolved_inverted_index(self):
        """Return a resolved inverted index with key as word and value as a set of url strings"""
        # First make an inverted lexicon mapping from word ids to words
        inverted_lexicon = {word_id: word for word, word_id in self._lexicon.items()}

        # Iterate through inverted index, replacing word/doc ids
        resolved_inverted_index = {}
        for word_id, doc_id_set in self._inverted_index.items():
            word = inverted_lexicon[word_id]
            urlset = set()
            for doc_id in doc_id_set: # Look up each doc_id in the document index
                url = self._document_index[doc_id-1][1]
                urlset.add(url)
        
            resolved_inverted_index[word] = urlset
        
        return resolved_inverted_index

    def generate_page_ranks(self, links):
        """Generate page ranks of links and store in database. Return pageranks dictionary"""
    
        page_ranks = pr.page_rank(links.keys())

        # (Sai) Insert into page rank table in db
        for doc_id, pagerank in page_ranks.items():
            query_str = "INSERT INTO pageRanks VALUES(%d, %f);" % (doc_id, pagerank)
            self._cur.execute(query_str)
            self._db_conn.commit()

        # Pages in page ranks dictionary:
	pages_in_pr = set(page_ranks.keys())

        # Pages in document index:
        doc_index_pages = set()
        for elem in self._document_index:
            doc_index_pages.add(elem[0])

        # Pages in document index but not page ranks
        missing_pages = doc_index_pages - pages_in_pr

        # Add missing pages to page rank table with rank of 0:
        for page in missing_pages:
            query_str = "INSERT INTO pageRanks VALUES(%d, 0);" % page
            self._cur.execute(query_str)
            self._db_conn.commit()
        
        return page_ranks

    def searchWord(self, word):
        """Return a list of URLS containing this word or -1 if not found"""
        resolved_inverted_index = self.get_resolved_inverted_index()
        if word not in resolved_inverted_index:
            return -1

        word_id = self._lexicon[word]
        doc_id_list = list(self._inverted_index[word_id])
        sorted_doc_id_list = self.sortDocIds(doc_id_list)
    
        return sorted_doc_id_list

    def sortDocIds(self, docid_list):
        # Make a new list
        sorted_list = list(docid_list)

        # Get page ranks
        ranks = self.generate_page_ranks(self._links)

    
        # Sort new list in place according to page ranks
    
        for j in range(1,len(sorted_list)):
            key = sorted_list[j]
            i = j - 1
            while i > -1 and ranks[sorted_list[i]] < ranks[key]:
                sorted_list[i+1] = sorted_list[i]
                i = i - 1
            sorted_list[i+1] = key
        
        return sorted_list


if __name__ == "__main__":
    con = lite.connect("dbFile.db")

    
    bot = crawler(con, "urls.txt")
    bot.crawl(depth=1)
    bot.generate_page_ranks(bot._links)

    print "\n\nDocument Index:", bot._document_index
    #print "\n\nLexicon:", bot._lexicon
    #print "\n\nInverted Index:", bot.get_inverted_index()
    #print "\n\nResolved Inverted Index:", bot.get_resolved_inverted_index()


    #print "\n\nDocument Index:"
    #for entry in bot._document_index:
        #print entry

    #print "\n\nLexicon:"
    #for word, word_id in bot._lexicon.items():
        #print "\t", word, ":\t", word_id

    #print "\n\nInverted Index:"
    #for word_id, doc_id_set in bot.get_inverted_index().items():
        #print "\t", word_id, ":\t", doc_id_set 

    #print "\n\nResolved Inverted Index:"
    #for word, urls in bot.get_resolved_inverted_index().items():
        #print "\t", word, ":\t", urls

    #print "\n\nLinks:"
    #print bot._links.keys()

    #print "\n\nPage Ranks:"
    #print pr.page_rank(bot._links.keys())


    
    #curs = con.cursor()

    #curs.execute("SELECT * FROM pageRanks;")
    #curs.execute("SELECT * FROM pageRanks;")
    #for row in curs:
    #    print row



