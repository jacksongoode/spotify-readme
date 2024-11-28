# Spotify Readme

A fork of a popular Spotify currently playing widget. Cleaned up and added a phrase for your daylist and a click through to redirect to last playing song.

There used to be a simple way of getting the day list which was to fetch the user's current playlists and iterate until you find the daylist. But since [November 27th, 2024](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api) Spotify has removed the ability to look up their curated generative playlists across all of their official endpoints.

This new implementation uses Playwright to sign in, search for the daylist, and grab the title from the Web app. It runs a cron job as an action every 30 minutes to update the daylist. We then fetch the artifact it generates using [nightly.link](https://nightly.link) and get the title from it.

## Preview

```
/svg
```

![Preview](https://spotify.jackson.gd/svg)

```
/link
```

[Link to the song](https://spotify.jackson.gd/link)

```
/daylist
/daylist/light
/daylist/dark
```

![Daylist](https://spotify.jackson.gd/daylist)

## Required variables

- `CLIENT_ID`
- `CLIENT_SECRET`
- `REFRESH_TOKEN`
- `SPOTIFY_USER`
- `SPOTIFY_PASS`
