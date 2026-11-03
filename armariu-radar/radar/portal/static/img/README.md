# Hero photo

`hero.jpg` is the photo behind the skyline banner. `skyline.js` paints it on a canvas and animates
it: a slow zoom and drift, ripples and sparkles on the water, the city's own lights twinkling,
blinking aviation lights and a passing plane. `hero.json` beside it tells the script where the
water starts, which point to keep in view when the photo is cropped, and where the aviation lights
are (all as fractions of the photo's width and height).

To use a different photo, replace `hero.jpg` (landscape, about 2400 px wide, under 600 KB) and
update `hero.json`, or delete it if the photo has no water. Without any photo, a drawn skyline
shows instead. Use a photo you have the rights to (your own, or one under a free licence such as
Unsplash's) before the site is hosted.
