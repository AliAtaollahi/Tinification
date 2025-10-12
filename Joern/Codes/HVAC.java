import java.util.Random;

public class HVAC {

    /* ------------ Controller ------------ */
    public static class Controller {
        // knownrebecs
        HC_Unit hc_unit;
        Meaningless meaningless;

        // statevars
        boolean heating_active;
        int sensedValue;

        public Controller() {
            heating_active = false;
            sensedValue = 20;
        }

        // msgsrv getSense(int temp) -> plain method
        public void getSense(int temp) {
            meaningless.start();
            meaningless.finish();

            sensedValue = temp;
            if (21 > temp && heating_active == false) {
                hc_unit.activateh(); // heat
                heating_active = true;
            } else if (21 <= temp && heating_active == true) {
                hc_unit.switchoff(); // switch off the heating process
                heating_active = false;
            }
        }
    }

    /* ------------ Room ------------ */
    public static class Room {
        // knownrebecs
        Sensor sensor;

        // statevars
        int temperature;
        int outside_air_blowing;
        int regulation;

        private final Random rnd = new Random();

        public Room() {
            // initial value
            temperature = 21;
            regulation = 0;
            outside_air_blowing = 0;

            // self.tempchange();  (WARNING: infinite recursion per the instruction)
            this.tempchange();
        }

        // msgsrv tempchange() -> plain method
        public void tempchange() {
            // environment effects the temperature slowly; here we just execute it synchronously
            outside_air_blowing = choose(1, 0);
            temperature = temperature - outside_air_blowing + regulation;
            sensor.getTemp(temperature);

            // self.tempchange() after(10);  --> after(10) is ignored as requested
            this.tempchange(); // WARNING: infinite recursion by design per instructions
        }

        // regulate temperature
        // msgsrv regulate(int v) -> plain method
        public void regulate(int v) {
            regulation = v;
        }

        private int choose(int a, int b) {
            // nondeterministic choice ?(1,0)
            return rnd.nextBoolean() ? a : b;
        }
    }

    /* ------------ Sensor ------------ */
    public static class Sensor {
        // knownrebecs
        Room room;
        Controller controller;

        // msgsrv getTemp(int temp) -> plain method
        public void getTemp(int temp) {
            controller.getSense(temp);
        }
    }

    /* ------------ HC_Unit ------------ */
    public static class HC_Unit {
        // knownrebecs
        Room room;

        // statevars
        boolean heater_on;

        public HC_Unit() {
            heater_on = false;
        }

        // msgsrv activateh() -> plain method
        public void activateh() {
            room.regulate(1);
            heater_on = true;
        }

        // msgsrv switchoff() -> plain method
        public void switchoff() {
            room.regulate(0);
            heater_on = false;
        }
    }

    /* ------------ Meaningless ------------ */
    public static class Meaningless {
        // knownrebecs
        Controller controller;

        // statevars
        boolean pointless;

        public Meaningless() {
            pointless = false;
        }

        // msgsrv start() -> plain method
        public void start() {
            pointless = true;
        }

        // msgsrv finish() -> plain method
        public void finish() {
            pointless = false;
        }
    }

    /* ------------ main wiring (as in your main block) ------------ */
    public static void main(String[] args) {
        // Construct all objects first (two-phase wiring to allow circular refs)
        Room room = new Room();               // WARNING: will recurse infinitely as written
        Controller controller = new Controller();
        Sensor sensor = new Sensor();
        HC_Unit hc_unit = new HC_Unit();
        Meaningless meaningless = new Meaningless();

        // Wire knownrebecs (matching your 'main' section)
        room.sensor = sensor;
        controller.hc_unit = hc_unit;
        controller.meaningless = meaningless;
        sensor.room = room;
        sensor.controller = controller;
        hc_unit.room = room;
        meaningless.controller = controller;

        // Nothing else to do; the Room constructor already called tempchange()
    }
}
