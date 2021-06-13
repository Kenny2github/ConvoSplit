# ConvoSplit
Split conversations into temporary channels, when multiple are happening in one.

## Usage
* Run `/split` to create a new temporary channel for your conversation.
  * The channel will be created with the same permission overrides as the channel you run the command from.
  * For example, if you run it in an admin-only channel, then the temporary channel will be admin-only too.
* By default, the temp channel is archived and deleted after 5 minutes of inactivity.
  * You can run `/split timeout: 3` to change that to 3 minutes (and other numbers work too, obviously).
  * You can run `/exit` *in the temp channel* to end the conversation early.
* Normally, the temp channel is open for anyone (who can access the original channel) to participate in.
  * You can run `/split member1: @user1` (up to `member5: @user5`) to limit the channel so that only the users mentioned (plus yourself) can send messages in it
  * However, everyone who can see the original channel can still read the conversation.
* Usually, the log of the conversation is sent to the channel where the command was run.
  * You can run `/split dest_channel: #channel` to send the log to that channel.
  * However, the bot must have permission to send files to that channel.
  * If it is unable to send it where you ask, it will try the original channel instead.
* All of the options mentioned above can be combined.
* Run `/invite` to get a link to add the bot to your server.