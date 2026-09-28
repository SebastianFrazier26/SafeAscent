--
-- PostgreSQL database dump
--

-- Dumped from database version 16.15 (eb11870)
-- Dumped by pg_dump version 16.13

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

--
-- Name: update_coordinates(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_coordinates() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            IF NEW.latitude IS NOT NULL AND NEW.longitude IS NOT NULL THEN
                NEW.coordinates = ST_SetSRID(ST_MakePoint(NEW.longitude, NEW.latitude), 4326)::geography;
            END IF;
            RETURN NEW;
        END;
        $$;

--
-- Name: accidents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.accidents (
    accident_id integer NOT NULL,
    source character varying(50),
    source_id character varying(100),
    date date,
    year double precision,
    state character varying(100),
    location text,
    mountain character varying(255),
    route character varying(255),
    latitude double precision,
    longitude double precision,
    elevation_meters double precision,
    accident_type character varying(100),
    activity character varying(100),
    injury_severity character varying(50),
    age_range character varying(50),
    description text,
    tags text,
    mountain_id integer,
    route_id integer,
    coordinates public.geography(Point,4326),
    mp_route_id bigint
);

--
-- Name: accidents_accident_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.accidents_accident_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: accidents_accident_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.accidents_accident_id_seq OWNED BY public.accidents.accident_id;

--
-- Name: area_weekly_weather; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.area_weekly_weather (
    weather_id integer NOT NULL,
    latitude numeric(5,2) NOT NULL,
    longitude numeric(6,2) NOT NULL,
    week_start date NOT NULL,
    week_end date NOT NULL,
    temp_avg numeric(5,1),
    temp_min numeric(5,1),
    temp_max numeric(5,1),
    precip_mm numeric(7,1),
    snow_cm numeric(6,1),
    wind_max_kmh numeric(5,1),
    wind_gust_max_kmh numeric(5,1),
    cloud_cover_avg numeric(4,1),
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

--
-- Name: area_weekly_weather_weather_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.area_weekly_weather_weather_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: area_weekly_weather_weather_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.area_weekly_weather_weather_id_seq OWNED BY public.area_weekly_weather.weather_id;

--
-- Name: ascents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ascents (
    ascent_id integer NOT NULL,
    route_id integer,
    climber_id integer,
    date date,
    style character varying(100),
    lead_style character varying(100),
    pitches integer,
    notes text,
    mp_tick_id character varying(50)
);

--
-- Name: ascents_ascent_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ascents_ascent_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: ascents_ascent_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ascents_ascent_id_seq OWNED BY public.ascents.ascent_id;

--
-- Name: climbers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.climbers (
    climber_id integer NOT NULL,
    username character varying(255) NOT NULL,
    mp_user_id character varying(50)
);

--
-- Name: climbers_climber_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.climbers_climber_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: climbers_climber_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.climbers_climber_id_seq OWNED BY public.climbers.climber_id;

--
-- Name: historical_predictions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.historical_predictions (
    id integer NOT NULL,
    route_id integer NOT NULL,
    prediction_date date NOT NULL,
    risk_score double precision NOT NULL,
    color_code character varying(10) NOT NULL,
    calculated_at timestamp without time zone DEFAULT now()
);

--
-- Name: historical_predictions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.historical_predictions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: historical_predictions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.historical_predictions_id_seq OWNED BY public.historical_predictions.id;

--
-- Name: mountains; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mountains (
    mountain_id integer NOT NULL,
    name character varying(255) NOT NULL,
    alt_names text,
    elevation_ft double precision,
    prominence_ft double precision,
    type character varying(50),
    range character varying(255),
    state character varying(100),
    latitude double precision,
    longitude double precision,
    location text,
    accident_count integer DEFAULT 0,
    coordinates public.geography(Point,4326)
);

--
-- Name: mountains_mountain_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.mountains_mountain_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: mountains_mountain_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.mountains_mountain_id_seq OWNED BY public.mountains.mountain_id;

--
-- Name: mp_locations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mp_locations (
    mp_id bigint NOT NULL,
    name character varying(500) NOT NULL,
    parent_id bigint,
    url character varying(500),
    latitude double precision,
    longitude double precision,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

--
-- Name: mp_routes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mp_routes (
    mp_route_id bigint NOT NULL,
    name character varying(500) NOT NULL,
    url character varying(500),
    location_id bigint,
    grade character varying(100),
    type character varying(100),
    length_ft double precision,
    pitches double precision,
    latitude double precision,
    longitude double precision,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

--
-- Name: mp_ticks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mp_ticks (
    tick_id integer NOT NULL,
    route_id character varying(20) NOT NULL,
    route_name character varying(255),
    climber_name character varying(255) NOT NULL,
    tick_date date,
    style character varying(50),
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

--
-- Name: mp_ticks_tick_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.mp_ticks_tick_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: mp_ticks_tick_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.mp_ticks_tick_id_seq OWNED BY public.mp_ticks.tick_id;

--
-- Name: routes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.routes (
    route_id integer NOT NULL,
    name character varying(255) NOT NULL,
    mountain_id integer,
    mountain_name character varying(255),
    grade character varying(50),
    grade_yds character varying(50),
    length_ft double precision,
    pitches integer,
    type character varying(100),
    first_ascent_year integer,
    latitude double precision,
    longitude double precision,
    accident_count integer DEFAULT 0,
    mp_route_id character varying(50),
    coordinates public.geography(Point,4326)
);

--
-- Name: routes_route_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.routes_route_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: routes_route_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.routes_route_id_seq OWNED BY public.routes.route_id;

--
-- Name: weather; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.weather (
    weather_id integer NOT NULL,
    accident_id integer,
    date date NOT NULL,
    latitude double precision NOT NULL,
    longitude double precision NOT NULL,
    temperature_avg double precision,
    temperature_min double precision,
    temperature_max double precision,
    wind_speed_avg double precision,
    wind_speed_max double precision,
    precipitation_total double precision,
    visibility_avg double precision,
    cloud_cover_avg double precision,
    coordinates public.geography(Point,4326)
);

--
-- Name: weather_weather_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.weather_weather_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: weather_weather_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.weather_weather_id_seq OWNED BY public.weather.weather_id;

--
-- Name: accidents accident_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accidents ALTER COLUMN accident_id SET DEFAULT nextval('public.accidents_accident_id_seq'::regclass);

--
-- Name: area_weekly_weather weather_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.area_weekly_weather ALTER COLUMN weather_id SET DEFAULT nextval('public.area_weekly_weather_weather_id_seq'::regclass);

--
-- Name: ascents ascent_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ascents ALTER COLUMN ascent_id SET DEFAULT nextval('public.ascents_ascent_id_seq'::regclass);

--
-- Name: climbers climber_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.climbers ALTER COLUMN climber_id SET DEFAULT nextval('public.climbers_climber_id_seq'::regclass);

--
-- Name: historical_predictions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historical_predictions ALTER COLUMN id SET DEFAULT nextval('public.historical_predictions_id_seq'::regclass);

--
-- Name: mountains mountain_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mountains ALTER COLUMN mountain_id SET DEFAULT nextval('public.mountains_mountain_id_seq'::regclass);

--
-- Name: mp_ticks tick_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mp_ticks ALTER COLUMN tick_id SET DEFAULT nextval('public.mp_ticks_tick_id_seq'::regclass);

--
-- Name: routes route_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routes ALTER COLUMN route_id SET DEFAULT nextval('public.routes_route_id_seq'::regclass);

--
-- Name: weather weather_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.weather ALTER COLUMN weather_id SET DEFAULT nextval('public.weather_weather_id_seq'::regclass);

--
-- Name: accidents accidents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accidents
    ADD CONSTRAINT accidents_pkey PRIMARY KEY (accident_id);

--
-- Name: area_weekly_weather area_weekly_weather_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.area_weekly_weather
    ADD CONSTRAINT area_weekly_weather_pkey PRIMARY KEY (weather_id);

--
-- Name: ascents ascents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ascents
    ADD CONSTRAINT ascents_pkey PRIMARY KEY (ascent_id);

--
-- Name: climbers climbers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.climbers
    ADD CONSTRAINT climbers_pkey PRIMARY KEY (climber_id);

--
-- Name: climbers climbers_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.climbers
    ADD CONSTRAINT climbers_username_key UNIQUE (username);

--
-- Name: historical_predictions historical_predictions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historical_predictions
    ADD CONSTRAINT historical_predictions_pkey PRIMARY KEY (id);

--
-- Name: historical_predictions historical_predictions_route_id_prediction_date_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historical_predictions
    ADD CONSTRAINT historical_predictions_route_id_prediction_date_key UNIQUE (route_id, prediction_date);

--
-- Name: mountains mountains_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mountains
    ADD CONSTRAINT mountains_pkey PRIMARY KEY (mountain_id);

--
-- Name: mp_locations mp_locations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mp_locations
    ADD CONSTRAINT mp_locations_pkey PRIMARY KEY (mp_id);

--
-- Name: mp_routes mp_routes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mp_routes
    ADD CONSTRAINT mp_routes_pkey PRIMARY KEY (mp_route_id);

--
-- Name: mp_ticks mp_ticks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mp_ticks
    ADD CONSTRAINT mp_ticks_pkey PRIMARY KEY (tick_id);

--
-- Name: routes routes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routes
    ADD CONSTRAINT routes_pkey PRIMARY KEY (route_id);

--
-- Name: area_weekly_weather unique_area_week; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.area_weekly_weather
    ADD CONSTRAINT unique_area_week UNIQUE (latitude, longitude, week_start);

--
-- Name: mp_ticks unique_tick; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mp_ticks
    ADD CONSTRAINT unique_tick UNIQUE (route_id, climber_name, tick_date);

--
-- Name: weather weather_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.weather
    ADD CONSTRAINT weather_pkey PRIMARY KEY (weather_id);

--
-- Name: idx_accidents_coords; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_accidents_coords ON public.accidents USING gist (coordinates);

--
-- Name: idx_accidents_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_accidents_date ON public.accidents USING btree (date);

--
-- Name: idx_accidents_mountain; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_accidents_mountain ON public.accidents USING btree (mountain_id);

--
-- Name: idx_accidents_mp_route_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_accidents_mp_route_id ON public.accidents USING btree (mp_route_id);

--
-- Name: idx_accidents_route; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_accidents_route ON public.accidents USING btree (route_id);

--
-- Name: idx_accidents_severity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_accidents_severity ON public.accidents USING btree (injury_severity);

--
-- Name: idx_accidents_state; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_accidents_state ON public.accidents USING btree (state);

--
-- Name: idx_accidents_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_accidents_type ON public.accidents USING btree (accident_type);

--
-- Name: idx_ascents_climber; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ascents_climber ON public.ascents USING btree (climber_id);

--
-- Name: idx_ascents_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ascents_date ON public.ascents USING btree (date);

--
-- Name: idx_ascents_route; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ascents_route ON public.ascents USING btree (route_id);

--
-- Name: idx_climbers_mp_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_climbers_mp_id ON public.climbers USING btree (mp_user_id);

--
-- Name: idx_climbers_username; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_climbers_username ON public.climbers USING btree (username);

--
-- Name: idx_historical_predictions_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_historical_predictions_date ON public.historical_predictions USING btree (prediction_date);

--
-- Name: idx_historical_predictions_route; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_historical_predictions_route ON public.historical_predictions USING btree (route_id);

--
-- Name: idx_mountains_coords; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mountains_coords ON public.mountains USING gist (coordinates);

--
-- Name: idx_mountains_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mountains_name ON public.mountains USING btree (name);

--
-- Name: idx_mountains_state; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mountains_state ON public.mountains USING btree (state);

--
-- Name: idx_mp_locations_coords; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mp_locations_coords ON public.mp_locations USING btree (latitude, longitude);

--
-- Name: idx_mp_locations_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mp_locations_name ON public.mp_locations USING btree (name);

--
-- Name: idx_mp_locations_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mp_locations_parent ON public.mp_locations USING btree (parent_id);

--
-- Name: idx_mp_routes_coords; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mp_routes_coords ON public.mp_routes USING btree (latitude, longitude);

--
-- Name: idx_mp_routes_grade; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mp_routes_grade ON public.mp_routes USING btree (grade);

--
-- Name: idx_mp_routes_location; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mp_routes_location ON public.mp_routes USING btree (location_id);

--
-- Name: idx_mp_routes_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mp_routes_name ON public.mp_routes USING btree (name);

--
-- Name: idx_mp_routes_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mp_routes_type ON public.mp_routes USING btree (type);

--
-- Name: idx_mp_ticks_climber; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mp_ticks_climber ON public.mp_ticks USING btree (climber_name);

--
-- Name: idx_mp_ticks_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mp_ticks_date ON public.mp_ticks USING btree (tick_date);

--
-- Name: idx_mp_ticks_route_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mp_ticks_route_id ON public.mp_ticks USING btree (route_id);

--
-- Name: idx_routes_coords; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_routes_coords ON public.routes USING gist (coordinates);

--
-- Name: idx_routes_mountain; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_routes_mountain ON public.routes USING btree (mountain_id);

--
-- Name: idx_routes_mp_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_routes_mp_id ON public.routes USING btree (mp_route_id);

--
-- Name: idx_routes_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_routes_name ON public.routes USING btree (name);

--
-- Name: idx_weather_accident; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_weather_accident ON public.weather USING btree (accident_id);

--
-- Name: idx_weather_coord_week; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_weather_coord_week ON public.area_weekly_weather USING btree (latitude, longitude, week_start);

--
-- Name: idx_weather_coords; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_weather_coords ON public.weather USING gist (coordinates);

--
-- Name: idx_weather_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_weather_date ON public.weather USING btree (date);

--
-- Name: idx_weather_week; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_weather_week ON public.area_weekly_weather USING btree (week_start);

--
-- Name: accidents accidents_coords_trigger; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER accidents_coords_trigger BEFORE INSERT OR UPDATE ON public.accidents FOR EACH ROW EXECUTE FUNCTION public.update_coordinates();

--
-- Name: mountains mountains_coords_trigger; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER mountains_coords_trigger BEFORE INSERT OR UPDATE ON public.mountains FOR EACH ROW EXECUTE FUNCTION public.update_coordinates();

--
-- Name: routes routes_coords_trigger; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER routes_coords_trigger BEFORE INSERT OR UPDATE ON public.routes FOR EACH ROW EXECUTE FUNCTION public.update_coordinates();

--
-- Name: weather weather_coords_trigger; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER weather_coords_trigger BEFORE INSERT OR UPDATE ON public.weather FOR EACH ROW EXECUTE FUNCTION public.update_coordinates();

--
-- Name: accidents accidents_mountain_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accidents
    ADD CONSTRAINT accidents_mountain_id_fkey FOREIGN KEY (mountain_id) REFERENCES public.mountains(mountain_id);

--
-- Name: accidents accidents_mp_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accidents
    ADD CONSTRAINT accidents_mp_route_id_fkey FOREIGN KEY (mp_route_id) REFERENCES public.mp_routes(mp_route_id);

--
-- Name: accidents accidents_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accidents
    ADD CONSTRAINT accidents_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.routes(route_id);

--
-- Name: ascents ascents_climber_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ascents
    ADD CONSTRAINT ascents_climber_id_fkey FOREIGN KEY (climber_id) REFERENCES public.climbers(climber_id);

--
-- Name: ascents ascents_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ascents
    ADD CONSTRAINT ascents_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.routes(route_id);

--
-- Name: mp_routes mp_routes_location_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mp_routes
    ADD CONSTRAINT mp_routes_location_id_fkey FOREIGN KEY (location_id) REFERENCES public.mp_locations(mp_id);

--
-- Name: routes routes_mountain_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routes
    ADD CONSTRAINT routes_mountain_id_fkey FOREIGN KEY (mountain_id) REFERENCES public.mountains(mountain_id);

--
-- Name: weather weather_accident_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.weather
    ADD CONSTRAINT weather_accident_id_fkey FOREIGN KEY (accident_id) REFERENCES public.accidents(accident_id);

--
-- PostgreSQL database dump complete
--
