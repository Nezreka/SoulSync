good call, and the count was only half of it. get missing was also grabbing episodes from the seasons you turned off, so pressing it would have pulled all 273.

fixed on dev:

- missing now means what soulsync is actually hunting, same as sonarr: aired, no file, monitored. an episode you unmonitored by hand counts the same as a whole unmonitored season. episodes that haven't aired yet never count.
- the show page says it apart, something like "2 aired episodes missing" with "271 more in unmonitored seasons are left alone" under it, and the library line shows "271 in unmonitored seasons" next to the episode count. nothing is hidden, it's just quieter.
- get missing, wishlist every missing episode and the hero's get missing count only take the monitored ones.
- the get modal still lists the unmonitored gaps so you can pick them by hand, but they start unticked with a "not monitored" tag, and select all missing skips them.

the missing episodes job in tools and the watchlist missing counts already only looked at monitored episodes, so those didn't change.

it'll be in the next build.
