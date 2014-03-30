###
# Copyright (c) 2012, DAn
# All rights reserved.
#
#
###

import supybot.conf as conf
import supybot.utils as utils
import supybot.ircdb as ircdb
from supybot.commands import *
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks

import os
import traceback
import datetime
import supybot.ircmsgs as ircmsgs
import supybot.schedule as schedule
import supybot.world as world
import threading

try:
    import sqlite3
except ImportError:
    from pysqlite2 import dbapi2 as sqlite3 # for python2.4

class Condorcet_Helper:
    """Condorcet voting method
    Vote with preference list and get most preferred option."""

    winners = []
    losers = []
    running = []
    scoreboard = []
    ranking = {}
    maprunscore = {}
    optioncount = 0
    curwinner = ""

    def __init__(self, optioncount):
        """create a Condorcet helper for a vote with optioncount options"""
        self.optioncount = optioncount
        self.winners = []
        self.losers = []
        self.running = []
        self._create_running()
        self.scoreboard = []
        self._create_scoreboard()
        self.ranking

    def _create_running(self):
        """Start with options 'A', 'B' etc """
        self.running = [chr(option) for option in xrange(ord('A'),ord('A')+self.optioncount)]

    def _create_scoreboard(self):
        """Create a 2d array for the running options"""
        curcount = len(self.running)
        self.optioncount = curcount
        self.scoreboard = []
        self.scoreboard = [[0 for x in range(curcount)] for y in range(curcount)]
        """
        for x in xrange(curcount):
            self.scoreboard.append([])
            for y in xrange(curcount):
                self.scoreboard[x].append(0)
        """

    def calc_vote(self, votes):
        """reset the scoreboard and add all the votes"""
        self._create_scoreboard()
        for vote in votes:
            self.add_vote(vote["vote"],vote["weight"])
        self.calc_winner()

    def calc_winner(self):
        """updates the winners according to the scoreboard"""
        running = []
        running[:] = self.running
        sb = self.scoreboard


        curcount = len(running)
        wins = [-0.5 for i in running]
        for w in range(curcount):
            for c in range(curcount):
                matchresult = sb[w][c] - sb[c][w]
                if matchresult > 0:
                    wins[w] += 1
                elif matchresult==0:
                    wins[w] += 0.5
            if wins[w]==curcount-1:
                # we have somebody that beat everybody, condorcet winner is w
                self.curwinner = running[w]
                return self.curwinner
        # we have no condorcet winner
        return None

        



    def add_vote(self,vote,weight=1):
        """
        Adds a condorcet preferencelist to the scoreboard
        <vote> a string formatted like 'B>A,C>!D' where '>' signifies preference, '!' signifies a vote against the preposition
        """
        votearray = vote.split(">")
        winners = []
        infavor = True
        for e in votearray:
            equals = []
            for option in e.split(","):
                if len(option)>1:
                    if option[0]=='!':
                        infavor = False
                        option = option[1:]
                c = ord(option)-ord('A')
                c = ord(option)-ord('A')
                equals.append(c)
                for w in winners:
                    self.scoreboard[w][c] += weight
            for i in equals:
                winners.append(i)

    def mod_vote(self,vote,mod):
        """in an existing vote add the options as specified in mod"""
        votearray = vote.split(">")
        winners = []
        infavor = True
        for e in votearray:
            equals = []
            for option in e.split(","):
                equals.append(option)
                if len(option)>1:
                    if option[0]=='!':
                        infavor = False
                        option = option[1:]
            winners.append(",".join(equals))






class Condorcet(callbacks.Plugin, plugins.ChannelDBHandler):
    """Poll for in channel
    Make polls and people can vote on them"""

    def __init__(self, irc):
        """run the usual init from parents"""
        callbacks.Plugin.__init__(self, irc)
        plugins.ChannelDBHandler.__init__(self)
        self.poll_schedules = [] # stores the current polls that are scheduled, so that on unload we can remove them

    def makeDb(self, filename):
        """ If db file exists, do connection and return it, else make new db and return connection to it"""

        if os.path.exists(filename):
            db = sqlite3.connect(filename, detect_types=sqlite3.PARSE_DECLTYPES|sqlite3.PARSE_COLNAMES)
            db.text_factory = str
            return db
        db = sqlite3.connect(filename, detect_types=sqlite3.PARSE_DECLTYPES|sqlite3.PARSE_COLNAMES)
        db.text_factory = str
        cursor = db.cursor()

        self._execute_query(cursor, """CREATE TABLE polls(
                    id INTEGER PRIMARY KEY,
                    started_time TIMESTAMP,         -- time when poll was created
                    isAnnouncing INTEGER default 1, -- if poll is announcing to channel
                    closed TIMESTAMP,               -- NULL by default, set to time when closed(no more voting allowed)
                    question TEXT,
                    deadline TIMESTAMP)""")
        self._execute_query(cursor, """CREATE TABLE choices(
                    poll_id INTEGER,
                    choice_char TEXT,
                    choice TEXT)""")
        self._execute_query(cursor, """CREATE TABLE votes(
                    id INTEGER PRIMARY KEY,
                    poll_id INTEGER,
                    voter_nick TEXT,
                    voter_host TEXT,
                    choice INTEGER,
                    time timestamp)""")
        db.commit()
        return db

    def getDb(self, channel):
        """Use this to get a database for a specific channel."""
        currentThread = threading.currentThread()
        if channel not in self.dbCache and currentThread == world.mainThread:
            self.dbCache[channel] = self.makeDb(self.makeFilename(channel))
        if currentThread != world.mainThread:
            db = self.makeDb(self.makeFilename(channel))
        else:
            db = self.dbCache[channel]
        try:
            db.autocommit = 1
        except AttributeError: # sqlite does not have autocommit, carry on anyway
            pass
        return db


    def _execute_query(self, cursor, queryString, *sqlargs):
        """ Executes a SqLite query
            in the given Db """

        try:
            if sqlargs:
                cursor.execute(queryString, sqlargs)
            else:
                cursor.execute(queryString)
        except Exception, e:
            self.log.error('Error with sqlite execute: %s' % e)
            self.log.error('For QueryString: %s' % queryString)
            raise

        return cursor

    def _poll_info(self, db, pollid):
        """ Does SQL query with 'db' for 'pollid' and returns isAnnouncing, closed, question
        or None if pollid doesnt exist

        ::isAnnouncing:: Integer 1 or 0
        ::closed:: None or datetime object
        ::question:: string""" 

        cursor = db.cursor()
        self._execute_query(cursor, 'SELECT isAnnouncing,closed,question FROM polls WHERE id=?', pollid)
        result = cursor.fetchone()
        if result is None:
            return

        return result[0], result[1], result[2]

    def _getwinner(self, db, pollid):
        """ Does SQL query with 'db' for 'pollid' and returns isAnnouncing, closed, question
        or None if pollid doesnt exist

        ::isAnnouncing:: Integer 1 or 0
        ::closed:: None or datetime object
        ::question:: string""" 

        cursor = db.cursor()
        self._execute_query(cursor, 'SELECT choice,count(*) FROM votes WHERE poll_id=? GROUP BY choice ORDER BY count(*) DESC', pollid)
        voteresult = cursor.fetchone()
        if voteresult is None:
            return 
        self._execute_query(cursor, 'SELECT choice FROM choices WHERE poll_id=? AND choice_char=?',pollid,voteresult[0])
        result = cursor.fetchone()
        if result is None:
            return 


        return result[0], voteresult[0], voteresult[1]

    def _runPoll(self, irc, channel, pollid):
        """Run by supybot schedule, outputs poll question and choices into channel at set interval"""

        db = self.getDb(channel)
        cursor = db.cursor()

        pollinfo = self._poll_info(db, pollid)
        if pollinfo is None:
            schedule.removeEvent('%s_poll_%s' % (channel, pollid))
            raise Exception('_runPoll couldnt get pollinfo')
        else:
            is_announcing, closed, question = pollinfo

        # if poll shouldnt be announcing or is closed, then stop schedule
        if (not is_announcing) or closed:
            try:
                schedule.removeEvent('%s_poll_%s' % (channel, pollid))
                self.poll_schedules.remove('%s_poll_%s' % (channel, pollid))
            except:
                self.log.warning('_runPoll Failed to remove schedule event for %s %s' % (channel, pollid))
            return

        irc.reply('Poll #%s: %s' % (pollid, question), prefixNick=False, to=channel)

        self._execute_query(cursor, 'SELECT choice_char,choice FROM choices WHERE poll_id=? ORDER BY choice_char', pollid)

        # output all of the polls choices
        choice_row = cursor.fetchone()
        while choice_row is not None:
            irc.reply('%s: %s' % (choice_row[0], choice_row[1]), prefixNick=False, to=channel)
            choice_row = cursor.fetchone()

        prefixChars = conf.supybot.reply.whenAddressedBy.chars()
        prefixStrings = conf.supybot.reply.whenAddressedBy.strings()
        prefixSubString = (' '.join(prefixStrings)).split(' ',1)[0]
        if prefixChars:
            vote_cmd = ''.join((prefixChars[:1],'vote'))
        elif prefixSubString:
            vote_cmd = ''.join((prefixSubString,'vote'))
        else:
            vote_cmd = ': '.join((irc.nick,'vote'))

        irc.reply('To vote, do %s %s <choice number>' % (vote_cmd, pollid), prefixNick=False, to=channel) 

    def newpoll(self, irc, msg, args, channel, interval, answers, question):
        """<number of minutes for announce interval> <"answer,answer,..."> question
        Creates a new poll with the given question and answers. <channel> is
        only necessary if the message isn't sent in the channel itself."""

        # capability = ircdb.makeChannelCapability(channel, 'op')
        # if not ircdb.checkCapability(msg.prefix, capability):
            # irc.error('Need ops')
            # return

        db = self.getDb(channel)
        cursor = db.cursor()
        self._execute_query(cursor, 'INSERT INTO polls VALUES (?,?,?,?,?)', None, datetime.datetime.now(), 1, None, question)
        pollid = cursor.lastrowid

        # used to add choices into db. each choice represented by character, starting at capital A (code 65)
        def genAnswers():
            for i, answer in enumerate(answers, start=65):
                yield pollid, chr(i), answer

        cursor.executemany('INSERT INTO choices VALUES (?,?,?)', genAnswers())

        db.commit()

        irc.reply('Started new poll #%s' % pollid)

        # function called by schedule event. can not have args
        def runPoll():
            self._runPoll(irc, channel, pollid)

        # start schedule. will announce poll/choices to channel at interval
        schedule.addPeriodicEvent(runPoll, interval*60, name='%s_poll_%s' % (channel, pollid))
        self.poll_schedules.append('%s_poll_%s' % (channel, pollid))

    newpoll = wrap(newpoll, ['channeldb', 'Op', 'positiveInt', commalist('something'), 'text'])

    def vote(self, irc, msg, args, channel, pollid, choice):
        """[<channel>] <poll id number> <choice letter>
        Vote for the option with the given <choice letter> on the poll with
        the given poll <id>. This command can also be used to override any
        previous vote. <channel> is only necesssary if the message isn't sent
        in the channel itself."""

        choice = choice.upper()
        db = self.getDb(channel)
        cursor = db.cursor()

        # query to check that poll exists and it isnt closed
        pollinfo = self._poll_info(db, pollid)
        if pollinfo is None:
            irc.error('No poll with that id')
            return
        if pollinfo[1] is not None:
            irc.error('This poll was closed on %s' % pollinfo[1].strftime('%Y-%m-%d at %-I:%M %p'))
            return

        # query to check that their choice exists
        self._execute_query(cursor, 'SELECT * FROM choices WHERE poll_id=? AND choice_char=?', pollid, choice)
        result = cursor.fetchone()
        if result is None:
            irc.error('That is not a choice for that poll')
            return

        # query to check they havnt already voted on this poll
        self._execute_query(cursor, 'SELECT choice,time FROM votes WHERE (voter_nick=? OR voter_host=?) AND poll_id=?', msg.nick, msg.host, pollid)
        result = cursor.fetchone()
        if result is not None:
            if result[0] == choice:
                irc.error('You have already voted for %s on %s' % (result[0], result[1].strftime('%Y-%m-%d at %-I:%M %p')))
                return
            else:
                # query to update their vote
                self._execute_query(cursor, 'UPDATE votes SET choice=?, time=? WHERE (voter_nick=? OR voter_host=?) AND poll_id=?', choice, datetime.datetime.now(), msg.nick, msg.host, pollid)
        else:
            # query to insert their vote
            self._execute_query(cursor, 'INSERT INTO votes VALUES (?,?,?,?,?,?)', None, pollid, msg.nick, msg.host, choice, datetime.datetime.now())
        db.commit()

        irc.reply('Your vote on poll #%s for %s has been inputed, sending you results in PM' % (pollid, choice), prefixNick=False)
        irc.reply('Here is results for poll #%s, you just voted for %s' % (pollid, choice), prefixNick=False, private=True)

        # query loop thru each choice for this poll, and for each choice another query to grab number of votes, and output
        cursor2 = db.cursor()
        self._execute_query(cursor, 'SELECT choice_char,choice FROM choices WHERE poll_id=? ORDER BY choice_char', pollid)
        choice_row = cursor.fetchone()
        while choice_row is not None:
            self._execute_query(cursor2, 'SELECT count(*) FROM votes WHERE poll_id=? AND choice=?', pollid, choice_row[0])
            vote_row = cursor2.fetchone()
            irc.reply('%s: %s - %s votes' % (choice_row[0], choice_row[1], vote_row[0]), prefixNick=False, private=True)
            choice_row = cursor.fetchone()

    vote = wrap(vote, ['channeldb', 'positiveInt', 'letter'])

    def results(self, irc, msg, args, channel, pollid):
        """[<channel>] <id>
        Privately shows the results for the poll with the given <id>.
        <channel> is only necessary if the message is not sent in the
        channel itself. You have to had voted already"""

        db = self.getDb(channel)
        cursor = db.cursor()
        pollinfo = self._poll_info(db, pollid)

        # query to make sure this poll exists. make new cursor since we will use it further below to output results
        cursor1 = db.cursor()
        self._execute_query(cursor1, 'SELECT choice_char,choice FROM choices WHERE poll_id=? ORDER BY choice_char', pollid)
        choice_row = cursor1.fetchone()
        if choice_row is None:
            irc.error('I dont think that poll id exists')
            return

        # query to make sure they have already voted on this poll
        self._execute_query(cursor, 'SELECT id FROM votes WHERE poll_id=? AND (voter_nick=? OR voter_host=?)', pollid, msg.nick, msg.host)
        result = cursor.fetchone()
        if result is None:
            irc.error('You need to vote first to view results!')
            return
        question = pollinfo[2]

        irc.reply('Here is results for poll #%s : %s' % (pollid, question) , prefixNick=False, private=True)

        # query loop thru each choice for this poll, and for each choice another query to grab number of votes, and output
        cursor2 = db.cursor()
        while choice_row is not None: 
            self._execute_query(cursor2, 'SELECT count(*) FROM votes WHERE poll_id=? AND choice=?', pollid, choice_row[0])
            vote_row = cursor2.fetchone()
            irc.reply('%s: %s - %s votes' % (choice_row[0], choice_row[1], vote_row[0]), prefixNick=False, private=True)
            choice_row = cursor1.fetchone()

    results = wrap(results, ['channeldb', 'positiveInt'])

    #TODO finish this command...
    def openpolls(self, irc, msg, args, channel):
        """[<channel>]
        Privately lists the currently open polls for <channel>. <channel> is
        only necessary if the message isn't sent in the channel itself."""
        db = self.getDb(channel)
        cursor = db.cursor()

        self._execute_query(cursor, 'SELECT id,question FROM polls WHERE closed is NULL')

        row = cursor.fetchone()
        while row is not None:
            irc.reply('Poll #%s: %s' % (row[0], row[1]), prefixNick=False, private=True)
            irc.reply('The choices are as follows :- ', prefixNick=False, private=True)
            cursorChoice = db.cursor()
            self._execute_query(cursorChoice, 'SELECT choice_char,choice FROM choices WHERE poll_id=? ORDER BY choice_char', row[0])
            choiceRow = cursorChoice.fetchone()
            while choiceRow is not None:
                irc.reply('%s: %s' % (choiceRow[0], choiceRow[1]), prefixNick=False, private=True)
                choiceRow = cursorChoice.fetchone()
            row = cursor.fetchone()

    openpolls = wrap(openpolls, ['channeldb'])

    def pollon(self, irc, msg, args, channel, pollid, interval):
        """<[channel]> <id> <interval in minutes>
        Schedules announcement of poll with the given <id> every <interval>.
        <channel> is only necessary if the message is not sent in the channel
        itself."""

        db = self.getDb(channel)
        cursor = db.cursor()

        # query to check poll exists, and if it is already on
        pollinfo = self._poll_info(db, pollid)
        if pollinfo is None:
            irc.error('That poll id does not exist')
            return
        if pollinfo[0] == 1:
            irc.error('Poll is already active')
            return

        # query to set poll off
        db.execute('UPDATE polls SET isAnnouncing=? WHERE id=?', (1, pollid))
        db.commit()

        if pollinfo[1] is not None:
            irc.reply('Note: you are turning on closed poll. I will not start announcing it')
            return

        # function called by schedule event. can not have args
        def runPoll():
            self._runPoll(irc, channel, pollid)

        # start schedule. will announce poll/choices to channel at interval
        schedule.addPeriodicEvent(runPoll, interval*60, name='%s_poll_%s' % (channel, pollid))
        self.poll_schedules.append('%s_poll_%s' % (channel, pollid))

    pollon = wrap(pollon, ['channeldb', 'Op', 'positiveInt', 'positiveInt'])

    def polloff(self, irc, msg, args, channel, pollid):
        """[<channel>] <id>
        Stops the poll with the given <id> from announcing. <channel> is
        only necessary if the message is not sent in the channel itself."""

        db = self.getDb(channel)
        cursor = db.cursor()

        # query to grab poll info, then check it exists, isnt already off, and warn them if it is closed
        pollinfo = self._poll_info(db, pollid)
        if pollinfo is None:
            irc.error('That poll id does not exist')
            return
        if pollinfo[0] == 0:
            irc.error('Poll is already off')
            return
        if pollinfo[1] is not None:
            irc.reply('Note: you are turning off a closed poll')

        # iquery to turn the poll "off", meaning it wont be scheduled to announce
        self._execute_query(cursor, 'UPDATE polls SET isAnnouncing=? WHERE id=?', 0, pollid)
        db.commit()

        try:
            schedule.removeEvent('%s_poll_%s' % (channel, pollid))
            self.poll_schedules.remove('%s_poll_%s' % (channel, pollid))
        except:
            irc.error('Removing scedule failed')
            return

        irc.replySuccess()

    polloff = wrap(polloff, ['channeldb', 'Op', 'positiveInt'])

    def closepoll(self, irc, msg, args, channel, pollid):
        """[channel] <id>
        Closes the poll with the given <id>. Further votes will not be
        allowed. <channel> is only necessary if the message isn't sent in
        the channel itself."""

        db = self.getDb(channel)
        cursor = db.cursor()

        # query to check poll exists and if it is closed
        pollinfo = self._poll_info(db, pollid)
        if pollinfo is None:
            irc.error('Poll id doesnt exist')
            return
        if pollinfo[1] is not None:
            irc.error('Poll already closed on %s' % pollinfo[1].strftime('%Y-%m-%d at %-I:%M %p'))
            return

        # close the poll in db
        self._execute_query(cursor, 'UPDATE polls SET closed=? WHERE id=?', datetime.datetime.now(), pollid)
        db.commit()
        
        winner = self._getwinner(db, pollid)
        question = pollinfo[2]
        irc.reply('Poll %s : "%s" was won by "%s"' % (pollid,question,winner[0]))

        try:
            schedule.removeEvent('%s_poll_%s' % (channel, pollid))
            self.poll_schedules.remove('%s_poll_%s' % (channel, pollid))
        except:
            self.log.warning('Failed to remove schedule event')
            return

        irc.replySuccess()

    closepoll = wrap(closepoll, ['channeldb', 'Op', 'positiveInt'])

    def openpoll(self, irc, msg, args, channel, pollid, interval):
        """[<channel>] <id>
        Starts announcing poll with the given <id> if set to active.
        <channel> is only necessary if the message isn't sent in the channel
        itself."""

        db = self.getDb(channel)
        cursor = db.cursor()

        # query to check poll exists and if it is open
        pollinfo = self._poll_info(db, pollid)
        if pollinfo is None:
            irc.error('Poll id doesnt exist')
            return
        if pollinfo[1] is None:
            irc.error('Poll is still open')
            return

        # query to OPEN IT UP! unsets closed time
        self._execute_query(cursor, 'UPDATE polls SET closed=? WHERE id=?', None, pollid)
        db.commit()

        # if poll was set active then start schedule for it
        if pollinfo[0] == 1:
            if interval is None:
                irc.reply('Note: Poll set to active, but you didnt supply interval, using default of 10 minutes')
                interval = 10
            # function called by schedule event. can not have args
            def runPoll():
                self._runPoll(irc, channel, pollid)

            # start schedule. will announce poll/choices to channel at interval
            schedule.addPeriodicEvent(runPoll, interval*60, name='%s_poll_%s' % (channel, pollid))
            self.poll_schedules.append('%s_poll_%s' % (channel, pollid))

    openpoll = wrap(openpoll, ['channeldb', 'Op', 'positiveInt', additional('positiveInt')])

    def die(self):
        for schedule_name in self.poll_schedules:
            schedule.removeEvent(schedule_name)

Class = Condorcet 

# vim:set shiftwidth=4 softtabstop=4 expandtab:
