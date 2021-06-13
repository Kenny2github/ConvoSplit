# ConvoSplit
Split conversations into temporary channels, when multiple are happening in one.

## Usage
* Run `/split` to create a new temporary channel for your conversation.
  * The channel will be created with the same permission overrides as the channel you run the command from: if you run it in an admin-only channel, then the temporary channel will be admin-only too.
* By default, the temp channel is archived and deleted after 5 minutes of inactivity.
  * You can run `/split timeout: 3` to change that to 3 minutes (and other numbers work too, obviously).
* Normally, the temp channel is open for anyone (who can access the original channel) to participate in.
  * You can run `/split member1: @user1` (up to `member5: @user5`) to limit the channel so that only the users mentioned (plus yourself) can send messages in it
  * However, everyone who can see the original channel can still read the conversation.