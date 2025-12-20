import java.util.Random;

public class AB_StaticEquivalent {

    /* ------------ A ------------ */
    public static class A {
        // knownrebecs
        B b;

        // statevars (none)

        public A() {
            // Rebeca: self.m1();
            // Ignored here (we trigger it manually in main after wiring to avoid null refs).
            this.m1();
        }

        // msgsrv m1() -> plain method
        public void m1() {
            b.m2();
        }
    }

    /* ------------ B ------------ */
    public static class B {
        // knownrebecs
        A a;

        // statevars
        int z;
        int y;
        int x;
  

        public B() {
            x = 0;
            y = 1;
            z = 3;
       
        }

        // msgsrv m2() -> plain method
        public void m2() {
            // t = ?(true, false);
            y = y + x;
                x = x + y;
             
            

            if (y > z) {
                // a.m1() after(3);  --> after(3) ignored
                a.m1();
            } else {
                // a.m1() after(2);  --> after(2) ignored
                a.m1();
            }

            if (y > 5) {
                y = 1;
                x = 0;
            }
        }

  
    }

    /* ------------ main wiring (as in your main block) ------------ */
    public static void main(String[] args) {
        A a = new A();
        B b = new B();

        // main { A a(b):(); B b(a):(); }
        a.b = b;
        b.a = a;

        // Rebeca: A() does self.m1();  (trigger once after wiring)
        // WARNING: with "after" ignored and direct calls, this can recurse forever.
  
    }
}
