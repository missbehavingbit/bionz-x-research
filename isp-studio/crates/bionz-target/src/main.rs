fn main() {
    for target in bionz_target::bundled() {
        println!("{}  {}", target.key, target.label);
    }
}
