rm -rf app
cp -r .. /tmp/dynomite-$$
mv /tmp/dynomite-$$ app
docker build -t notbt/dynomite .
