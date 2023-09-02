Personal notes on building the docker image
1. Edit docker file to build locally:
   1. Remove `image: NaruZosa/jncep_webui`
   2. Add `build: .` where `image: NaruZosa/jncep_webui` was
2. `docker-compose up`
   1. For testing only. Confirm the web UI works and downloads work. 
3. `docker build --tag NaruZosa/jnovelclub --tag NaruZosa/jnovelclub:v38.0 .`  (or whatever version jncep is at, and is listed in the requirements as)
4. `docker push NaruZosa/jnovelclub:v38.0`  (or whatever version jncep is at, and is listed in the requirements as)
5. `docker push NaruZosa/jnovelclub:latest`